"""Dashboard API. The React app polls these endpoints every 2 seconds.

Replay controls (play, pause, step, speed) run the clock in a background thread;
every mutation goes through one lock so the UI and the clock never interleave.
"""
from __future__ import annotations

import statistics
import threading
import time
from collections import Counter, defaultdict
from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, select, text

from bp import calendar
from bp.agents import approvals
from bp.agents.auditor import Auditor
from bp.agents.events import jsonable
from bp.calendar import bd_between
from bp.db import (approvals as approvals_t, breaks, cases, engine, events, ledger_entries, ledger_lines, messages,
                   playbooks, proposals, sim_state, teach_sessions)
from bp.engine.replay import Replay
from bp.eval import evaluate
from bp.playbooks import store as pbstore
from bp.playbooks.library import ACTIONS
from bp.recon.context import FACTS
from bp.sources.api import KEYS, SourceClient, handlers
from bp.world.live import TYPES as INJECT_TYPES

SPEEDS = {"slow": 2.0, "normal": 0.6, "fast": 0.0}
TYPE_OF_KEY = {
    "ledger-custodian:trade_qty": "Trade quantity", "ledger-fee_schedule:commission": "Commission",
    "ledger-custodian:cash": "Cash", "ledger-custodian:position_qty": "Position", "ledger-bank:cash": "Bank / ACH",
    "ledger-custodian:principal": "Rounding", "ledger-custodian:settlement_timing": "T+1 timing",
}


class Decision(BaseModel):
    decision: str  # approved | rejected
    reason: str = ""


class Teach(BaseModel):
    trusted_source: str
    actions: list[str]
    fact: str
    note: str
    teacher: str = "Dana Ortiz"


class Inject(BaseModel):
    type: str
    account_id: str
    symbol: str


class Ask(BaseModel):
    case_id: str
    question: str


class Mode(BaseModel):
    controller: str | None = None
    teach: str | None = None
    speed: str | None = None


class Runner:
    def __init__(self, rp: Replay):
        self.rp = rp
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.stop = threading.Event()
        self.last_error: str | None = None

    def speed(self) -> str:
        with self.rp.eng.connect() as c:
            return c.execute(select(sim_state.c.speed)).scalar() or "fast"

    def step(self) -> dict | None:
        with self.lock:
            done = self.rp.last_done()
            days = [d for d in calendar.business_days() if not done or d > done]
            if not days:
                return None
            return self.rp.run_day(days[0])

    def _loop(self):
        try:
            while not self.stop.is_set():
                if self.step() is None:
                    break
                time.sleep(SPEEDS.get(self.speed(), 0))
        except Exception as e:  # surface in the header instead of dying silently
            self.last_error = f"{type(e).__name__}: {e}"
        finally:
            with self.rp.eng.begin() as c:
                c.execute(sim_state.update().values(running=False))

    def play(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        with self.rp.eng.begin() as c:
            c.execute(sim_state.update().values(running=True))
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def pause(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=30)


def _rows(conn, q) -> list[dict]:
    return [jsonable(dict(r)) for r in conn.execute(q).mappings()]


def create_app(eng=None, replay: Replay | None = None) -> FastAPI:
    eng = eng or engine()
    if replay is None:
        replay = Replay(eng=eng, controller_mode="human", teach_mode="human")
        with eng.connect() as c:
            has = c.execute(text("SELECT to_regclass('sim_state')")).scalar()
        replay.init(fresh=not has)
    rp = replay
    runner = Runner(rp)
    app = FastAPI(title="Breakpoint dashboard API")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.state.runner = runner

    # ------------------------------------------------------------------ replay controls
    @app.get("/api/state")
    def state():
        with eng.connect() as c:
            st = jsonable(rp.state(c))
            counts = dict(c.execute(select(cases.c.status, func.count()).group_by(cases.c.status)).all())
            pending = c.execute(select(func.count()).select_from(proposals).where(
                proposals.c.status == "pending", proposals.c.route == "controller")).scalar()
            drafts = c.execute(select(func.count()).select_from(playbooks).where(playbooks.c.status == "draft")).scalar()
        from bp.agents import llm
        return {**st, "running": bool(runner.thread and runner.thread.is_alive()), "case_counts": counts,
                "pending_approvals": pending + drafts, "needs_human": counts.get("needs_human", 0),
                "llm": llm.backend(), "error": runner.last_error, "start": calendar.START.isoformat(),
                "end": calendar.END.isoformat(), "days_total": len(calendar.business_days())}

    @app.post("/api/replay/play")
    def play():
        runner.play()
        return {"ok": True}

    @app.post("/api/replay/pause")
    def pause():
        runner.pause()
        return {"ok": True}

    @app.post("/api/replay/step")
    def step():
        runner.pause()
        return {"day": runner.step()}

    @app.post("/api/replay/reset")
    def reset_():
        runner.pause()
        with runner.lock:
            with eng.connect() as c:
                st = rp.state(c)
            rp.store.injected.clear()
            from bp.sources.store import SourceStore
            rp.store.__init__(rp.store.dir)  # drop live injections from memory
            rp.init(fresh=True)
            with eng.begin() as c:
                c.execute(sim_state.update().values(controller_mode=st["controller_mode"], teach_mode=st["teach_mode"],
                                                    speed=st["speed"]))
        return {"ok": True}

    @app.post("/api/mode")
    def mode(m: Mode):
        vals = {k: v for k, v in {"controller_mode": m.controller, "teach_mode": m.teach, "speed": m.speed}.items() if v}
        with eng.begin() as c:
            c.execute(sim_state.update().values(**vals))
        return {"ok": True}

    # ------------------------------------------------------------------ overview
    @app.get("/api/overview")
    def overview():
        with eng.connect() as c:
            st = rp.state(c)
            today = st["last_done"]
            cs = [dict(r) for r in c.execute(select(cases)).mappings()]
            bs = c.execute(select(breaks.c.detected_on, breaks.c.harmless, breaks.c.break_key, breaks.c.status)).all()
        by_status = Counter(x["status"] for x in cs)
        open_cs = [x for x in cs if x["status"] not in ("completed", "auto_cleared")]
        closed = [x for x in cs if x["status"] == "completed" and x["closed_on"]]
        ttr = [bd_between(x["opened_on"], x["closed_on"]) for x in closed]
        auto = sum(1 for x in closed if x["completion"] and x["completion"].get("playbook"))
        per_day = defaultdict(lambda: {"breaks": 0, "harmless": 0})
        for d, harmless, key, _ in bs:
            per_day[d.isoformat()]["harmless" if harmless else "breaks"] += 1
        series = [{"date": d.isoformat(), **per_day.get(d.isoformat(), {"breaks": 0, "harmless": 0})}
                  for d in calendar.business_days() if today and d <= today]
        buckets = Counter()
        for x in open_cs:
            age = bd_between(x["opened_on"], today) if today else 0
            buckets["0-1d" if age <= 1 else "2-3d" if age <= 3 else "4-10d" if age <= 10 else ">10d"] += 1
        harmless = sum(1 for b in bs if b[1] and b[3] == "auto_cleared")
        return jsonable({
            "today": today, "by_status": dict(by_status), "open_cases": len(open_cs),
            "dollars_at_risk": sum((x["amount_usd"] for x in open_cs), 0),
            "auto_resolution_rate": round(100 * auto / len(cs), 1) if cs else None,
            "auto_resolved": auto, "total_cases": len(cs), "harmless_auto_cleared": harmless,
            "median_days_to_resolve": statistics.median(ttr) if ttr else None, "series": series,
            "aging": [{"bucket": k, "cases": buckets.get(k, 0)} for k in ["0-1d", "2-3d", "4-10d", ">10d"]],
        })

    # ------------------------------------------------------------------ breaks and cases
    @app.get("/api/breaks")
    def list_breaks(include_harmless: bool = False):
        with eng.connect() as c:
            today = rp.state(c)["last_done"]
            q = select(breaks, cases.c.status.label("case_status"), cases.c.playbook_id, cases.c.branch_id).outerjoin(
                cases, cases.c.case_id == breaks.c.case_id).order_by(breaks.c.detected_on.desc(), breaks.c.break_id)
            if not include_harmless:
                q = q.where(breaks.c.harmless.is_(False))
            rows = _rows(c, q)
            agent = {r.case_id: r.actor for r in c.execute(text(
                "SELECT DISTINCT ON (case_id) case_id, actor FROM events WHERE case_id IS NOT NULL ORDER BY case_id, seq DESC"))}
        for r in rows:
            r["type"] = TYPE_OF_KEY.get(r["break_key"], r["break_key"])
            r["age"] = bd_between(date.fromisoformat(r["detected_on"]), today) if today else 0
            r["agent"] = agent.get(r["case_id"])
            r["status_label"] = r["case_status"] or r["status"]
        return rows

    @app.get("/api/cases/{case_id}")
    def case_detail(case_id: str):
        with eng.connect() as c:
            cs = c.execute(select(cases).where(cases.c.case_id == case_id)).mappings().first()
            if not cs:
                raise HTTPException(404, "no such case")
            bks = _rows(c, select(breaks).where(breaks.c.case_id == case_id).order_by(breaks.c.break_id))
            evs = _rows(c, select(events).where(events.c.case_id == case_id).order_by(events.c.seq))
            props = _rows(c, select(proposals).where(proposals.c.case_id == case_id).order_by(proposals.c.proposal_id))
            pids = [p["proposal_id"] for p in props]
            apps = _rows(c, select(approvals_t).where(approvals_t.c.proposal_id.in_(pids))) if pids else []
            msgs = _rows(c, select(messages).where(messages.c.case_id == case_id).order_by(messages.c.msg_id))
            ts = _rows(c, select(teach_sessions).where(teach_sessions.c.case_id == case_id))
            fixes = _rows(c, select(ledger_entries).where(ledger_entries.c.proposal_id.in_(pids))) if pids else []
            pb = None
            if cs["playbook_id"]:
                p = pbstore.get(c, cs["playbook_id"], cs["playbook_version"])
                pb = p.dump() if p else None
        return {"case": jsonable(dict(cs)), "breaks": bks, "events": evs, "proposals": props, "approvals": apps,
                "messages": msgs, "teach_sessions": ts, "ledger_fixes": fixes, "playbook": pb}

    @app.get("/api/breaks/{break_id}")
    def break_detail(break_id: str):
        with eng.connect() as c:
            b = c.execute(select(breaks).where(breaks.c.break_id == break_id)).mappings().first()
        if not b:
            raise HTTPException(404, "no such break")
        return {"break": jsonable(dict(b)), **(case_detail(b["case_id"]) if b["case_id"] else {})}

    # ------------------------------------------------------------------ approvals (controller queue)
    @app.get("/api/approvals")
    def approvals_queue():
        with eng.connect() as c:
            ps = approvals.pending_for_controller(c)
            out = []
            for p in ps:
                cs = c.execute(select(cases.c.summary, cases.c.break_key, cases.c.status).where(
                    cases.c.case_id == p["case_id"])).mappings().first()
                bks = _rows(c, select(breaks).where(breaks.c.case_id == p["case_id"]))
                out.append({**jsonable(p), "case": jsonable(dict(cs)) if cs else None, "breaks": bks})
            drafts = _rows(c, select(playbooks).where(playbooks.c.status == "draft").order_by(playbooks.c.id))
        return {"proposals": out, "playbooks": drafts}

    @app.post("/api/approvals/{pid}")
    def decide(pid: str, d: Decision):
        with runner.lock, eng.begin() as c:
            today = rp.state(c)["last_done"] or calendar.START
            try:
                approvals.decide(c, pid, approvals.CONTROLLER, d.decision, d.reason or f"{d.decision} in the UI", today)
            except (approvals.SelfApproval, approvals.PolicyViolation) as e:
                raise HTTPException(400, str(e))
        if rp.workflow:
            with runner.lock:
                rp.workflow.work(today)
        return {"ok": True}

    # ------------------------------------------------------------------ playbooks
    @app.get("/api/playbooks")
    def list_playbooks():
        with eng.connect() as c:
            return _rows(c, select(playbooks).order_by(playbooks.c.id, playbooks.c.version.desc()))

    @app.post("/api/playbooks/{pid}/{version}/{action}")
    def playbook_action(pid: str, version: int, action: str):
        with runner.lock, eng.begin() as c:
            today = rp.state(c)["last_done"] or calendar.START
            if action == "approve":
                rp.workflow.approve_playbook(c, pid, version, approvals.CONTROLLER, today, "approved in the UI")
            elif action in ("flag", "retire"):
                pbstore.set_status(c, pid, version, "flagged" if action == "flag" else "retired", approvals.CONTROLLER,
                                   f"{action} in the UI", today)
            else:
                raise HTTPException(400, "action must be approve, flag or retire")
        if action == "approve" and rp.workflow:
            with runner.lock:
                rp.workflow.work(today)
        return {"ok": True}

    # ------------------------------------------------------------------ teach mode
    @app.get("/api/teach/{case_id}")
    def teach_form(case_id: str):
        det = case_detail(case_id)
        key = det["case"]["break_key"]
        return {**det, "facts_available": FACTS.get(key, []),
                "sources": ["ledger", "custodian", "executions", "bank", "fee_schedule", "corporate_actions"],
                "actions": {k: v.model_dump() for k, v in ACTIONS.items()}}

    @app.post("/api/teach/{case_id}")
    def teach(case_id: str, t: Teach):
        with runner.lock, eng.begin() as c:
            today = rp.state(c)["last_done"] or calendar.START
            try:
                out = rp.workflow.teach(c, case_id, teacher=t.teacher, trusted_source=t.trusted_source,
                                        actions=t.actions, fact=t.fact, note=t.note, d=today)
            except Exception as e:
                raise HTTPException(400, f"{type(e).__name__}: {e}")
        with runner.lock:
            rp.workflow.work(today)
        return out

    # ------------------------------------------------------------------ proof, inject, ask, sources, audit
    @app.get("/api/proof")
    def proof():
        with eng.connect() as c:
            return jsonable(evaluate(c, rp.store))

    @app.get("/api/inject/options")
    def inject_options():
        return {"types": INJECT_TYPES, "accounts": [a.model_dump(mode="json") for a in rp.store.accounts],
                "symbols": [s.symbol for s in rp.store.symbols]}

    @app.post("/api/inject")
    def inject(i: Inject):
        with runner.lock:
            try:
                return rp.inject(i.type, i.account_id, i.symbol)
            except ValueError as e:
                raise HTTPException(400, str(e))

    @app.post("/api/ask")
    def ask(a: Ask):
        with eng.connect() as c:
            return Auditor().ask(c, a.question, a.case_id)

    @app.get("/api/sources/{source}/{endpoint}")
    def source(source: str, endpoint: str, d: date):
        if source not in KEYS:
            raise HTTPException(404, "unknown source")
        return SourceClient(source, KEYS[source], rp.store).get(endpoint, d)

    @app.get("/api/sources")
    def source_index():
        return {s: list(eps) for s, eps in handlers(rp.store).items()}

    @app.get("/api/messages")
    def all_messages():
        with eng.connect() as c:
            return _rows(c, select(messages).order_by(messages.c.msg_id.desc()).limit(500))

    @app.get("/api/events")
    def audit(q: str = "", limit: int = 300):
        with eng.connect() as c:
            qq = select(events).order_by(events.c.seq.desc()).limit(limit)
            if q:
                like = f"%{q}%"
                qq = qq.where(events.c.reason.ilike(like) | events.c.action.ilike(like) | events.c.actor.ilike(like)
                              | events.c.case_id.ilike(like))
            return _rows(c, qq)

    @app.get("/api/ledger/{account_id}")
    def ledger_account(account_id: str):
        with eng.connect() as c:
            return _rows(c, select(ledger_lines.c.asset, func.sum(ledger_lines.c.amount).label("balance")).where(
                ledger_lines.c.account.like(f"client:{account_id}%")).group_by(ledger_lines.c.asset))

    return app


_app = None


def __getattr__(name):  # lazy, so importing the module (tests) does not touch the demo database
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
