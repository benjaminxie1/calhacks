"""Simulated clock (R23): replays the reconciliation season one business day at a time.

Daily loop: lock due periods, ingest the day's AP booking feed into the ledger (estimate
payments and the statements that arrived), recompute each new statement against its
lease, let agents work open cases, then aging. Cases, playbooks and their history
persist across days, so a lesson learned in March changes what happens in May.
"""
from __future__ import annotations

import time
from datetime import date

from sqlalchemy import select

from bp import calendar, config
from bp.agents.events import emit
from bp.db import engine, init_schema, injections, reset, sim_state
from bp.ledger import service as ledger
from bp.recon.matcher import Matcher
from bp.sources import Sources
from bp.sources.store import SourceStore, get_store


class Replay:
    def __init__(self, eng=None, store: SourceStore | None = None, seed: int = config.SEED, clean: bool = False,
                 agents: bool = True, controller_mode: str = "scripted", teach_mode: str = "human"):
        self.eng = eng or engine()
        self.store = store or get_store(seed, clean)
        self.sources = Sources(self.store)
        self.matcher = Matcher(self.sources)
        self.seed, self.clean = seed, clean
        self.agents = agents
        self.controller_mode, self.teach_mode = controller_mode, teach_mode
        self._booking_cred = ledger.issue_credential("booking-system")
        self.workflow = None
        if agents:
            from bp.engine.workflow import Workflow

            self.workflow = Workflow(self)

    # ------------------------------------------------------------------ state
    def init(self, fresh: bool = True) -> None:
        if fresh:
            reset(self.eng)
        else:
            init_schema(self.eng)
        with self.eng.begin() as c:
            ledger.ensure_periods(c)
            if not c.execute(select(sim_state.c.id)).first():
                c.execute(sim_state.insert().values(id=1, seed=self.seed, clean=self.clean,
                                                    controller_mode=self.controller_mode, teach_mode=self.teach_mode))
            if self.workflow:
                self.workflow.bootstrap(c)

    def state(self, conn) -> dict:
        return dict(conn.execute(select(sim_state)).mappings().first())

    def last_done(self) -> date | None:
        with self.eng.connect() as c:
            return c.execute(select(sim_state.c.last_done)).scalar()

    def load_injections(self, conn) -> None:
        for r in conn.execute(select(injections)):
            self.store.apply_injection(r.id, r.records)

    def inject(self, kind: str, lease_id: str, on: date | None = None) -> dict:
        """Plant an error in a statement that lands on the next replay day (judge-driven, R26)."""
        from bp.world.live import build

        done = self.last_done()
        on = on or (calendar.next_bd(done) if done else calendar.START)
        with self.eng.begin() as c:
            seq = c.execute(select(injections.c.id)).all()
            inj_id = f"INJ-{len(seq) + 1:03d}"
            recs = build(self.store, kind, lease_id, on, len(seq) + 1)
            c.execute(injections.insert().values(id=inj_id, effective_on=on, spec={"type": kind, "lease_id": lease_id},
                                                 records=recs, answer_key=recs["answer_key"]))
            emit(c, "judge", "break_injected", f"{kind} on {lease_id}'s statement, lands {on}",
                 inputs={"injection": inj_id, "statement": recs["statements"][0]["statement_id"]}, sim_date=on)
        self.store.apply_injection(inj_id, recs)
        return {"injection": inj_id, "effective_on": on.isoformat(), "statement_id": recs["statements"][0]["statement_id"],
                "answer_key": recs["answer_key"][0]}

    # ------------------------------------------------------------------ the daily loop
    def run_day(self, d: date) -> dict:
        t0 = time.time()
        out = {"date": d.isoformat()}
        with self.eng.begin() as c:
            self.load_injections(c)
            c.execute(sim_state.update().values(current_date=d))
            closed = ledger.close_due_periods(c, d)
            for p in closed:
                emit(c, "clock", "period_closed", f"{p} locked on its 3rd business day", sim_date=d)
            out["posted"] = ledger.post_feed(c, self._booking_cred, self.store.booking_feed(d))
            out["breaks"] = self.matcher.run_transaction(c, d)
            self.matcher.recon_timing(c, d)
        if self.workflow:
            self.workflow.work(d)
        with self.eng.begin() as c:
            out["breaks"] += self.matcher.recon_eod(c, d)
        if self.workflow:
            self.workflow.work(d)
            self.workflow.aging(d)
        with self.eng.begin() as c:
            c.execute(sim_state.update().values(last_done=d))
        out["secs"] = round(time.time() - t0, 3)
        return out

    def run(self, start: date | None = None, end: date | None = None, on_day=None) -> list[dict]:
        start = start or calendar.START
        end = end or calendar.END
        done = self.last_done()
        res = []
        for d in calendar.business_days():
            if d < start or d > end or (done and d <= done):
                continue
            r = self.run_day(d)
            res.append(r)
            if on_day:
                on_day(r)
        return res


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default=calendar.START.isoformat())
    ap.add_argument("--to", dest="end", default=calendar.END.isoformat())
    ap.add_argument("--seed", type=int, default=config.SEED)
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--no-agents", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--controller", default="scripted", choices=["scripted", "human"])
    ap.add_argument("--teach", default="human", choices=["scripted", "human"])
    a = ap.parse_args()
    rp = Replay(seed=a.seed, clean=a.clean, agents=not a.no_agents, controller_mode=a.controller, teach_mode=a.teach)
    rp.init(fresh=not a.resume)
    t0 = time.time()
    rp.run(date.fromisoformat(a.start), date.fromisoformat(a.end),
           on_day=lambda r: print(f"{r['date']}  posted {r['posted']:4d}  new breaks {r['breaks']:3d}  {r['secs']}s"))
    print(f"replay finished in {time.time() - t0:.1f}s")
