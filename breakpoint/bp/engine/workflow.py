"""Case workflow: runs every open break through the agent flow.

    matcher -> investigator -> playbook fits?
       yes: preparer -> approval (agent <= $2,500, else controller) -> executor -> verify
       no:  needs human -> teach mode -> learner drafts -> controller approves -> library

Case statuses: new, investigating, waiting, awaiting_approval, completed,
auto_cleared, needs_human. A completed case cites the playbook id, version and
branch, the proposals executed and the verify result.
"""
from __future__ import annotations

import traceback
from datetime import date

from sqlalchemy import select, text

from bp.agents import approvals
from bp.agents.core import ApproverAgent, Investigator, Preparer
from bp.agents.events import emit, jsonable
from bp.agents.executor import Executor
from bp.agents.learner import draft_from_teach, induct, load_history
from bp.agents.outreach import Outreach, Personas
from bp.db import breaks, cases, messages, proposals, sim_state, teach_sessions
from bp.money import D, s
from bp.playbooks import store as pbstore
from bp.playbooks.library import ACTIONS
from bp.recon import matcher as M

OPEN = ("new", "investigating", "waiting", "awaiting_approval")
BOOTSTRAP_DAY = date(2026, 1, 30)

# What the lease admin lead teaches for each novel case in scripted mode (the demo's "Dana" answers).
SCRIPTED_LESSONS = {
    M.K_GROSS: {"trusted_source": "lease", "actions": ["gross_up_credit", "dispute_notice"], "fact": "gross_up_not_in_lease",
                "note": "Our lease has no gross-up clause, so the landlord can't scale janitorial and utilities up to 95% "
                        "occupancy. Credit our share of the whole gross-up and send the landlord a dispute notice."},
}


class Workflow:
    def __init__(self, replay):
        self.rp = replay
        self.eng = replay.eng
        self.m = replay.matcher
        self.src = replay.sources
        self.investigator = Investigator(self.m)
        self.preparer = Preparer(self.m)
        self.approver = ApproverAgent()
        self.executor = Executor()
        self.outreach = Outreach(self.src)
        self.personas = Personas(self.src)

    # ------------------------------------------------------------------ setup
    def bootstrap(self, conn) -> None:
        """Induction from last season's cases + SOP; the controller approves the drafts before the season opens."""
        if pbstore.all_rows(conn):
            return
        cases_, sop = load_history()
        for pb in induct(cases_, sop, BOOTSTRAP_DAY, conn):
            pbstore.save(conn, pb, "learner", BOOTSTRAP_DAY,
                         note=f"induction draft from {len(cases_)} cases from last season + SOP.md")
            self.approve_playbook(conn, pb.id, pb.version, approvals.CONTROLLER, BOOTSTRAP_DAY,
                                  "reviewed induction draft against last season's disputes and the SOP")

    def mode(self, conn) -> dict:
        r = conn.execute(select(sim_state.c.controller_mode, sim_state.c.teach_mode)).first()
        return {"controller": r.controller_mode if r else "scripted", "teach": r.teach_mode if r else "human"}

    # ------------------------------------------------------------------ daily entry points
    def work(self, d: date) -> None:
        with self.eng.begin() as c:
            mode = self.mode(c)
            self.personas.respond(c, d)
            if mode["controller"] == "scripted":
                self.scripted_controller(c, d)
            self.open_cases(c, d)
        for _ in range(6):
            progressed = False
            with self.eng.connect() as c:
                ids = [r.case_id for r in c.execute(select(cases.c.case_id).where(cases.c.status.in_(OPEN))
                                                    .order_by(cases.c.case_id))]
            for cid in ids:
                progressed |= self.step_safe(cid, d)
            with self.eng.begin() as c:
                if self.mode(c)["teach"] == "scripted":
                    progressed |= self.scripted_teacher(c, d)
            if not progressed:
                break

    def aging(self, d: date) -> None:
        with self.eng.begin() as c:
            for case in [dict(r) for r in c.execute(select(cases).where(cases.c.status == "waiting")).mappings()]:
                res = self.outreach.aging(c, case, d)
                if res == "escalated":
                    self._set(c, case, d, status="needs_human", escalated=True, waiting_on="controller",
                              note=f"no reply from {case['waiting_on']} after 3 business days; escalated to the controller")

    # ------------------------------------------------------------------ cases
    def open_cases(self, conn, d: date) -> int:
        rows = [dict(r) for r in conn.execute(select(breaks).where(
            breaks.c.status == "open", breaks.c.harmless.is_(False), breaks.c.case_id.is_(None))
            .order_by(breaks.c.break_id)).mappings()]
        n = 0
        for b in rows:
            # one landlord grossing up several of our stores in the same mailing is one case
            group = (f"{b['break_key']}:{b['symbol']}:{b['detected_on']}" if b["break_key"] == M.K_GROSS
                     else b["detection_key"])
            cid = conn.execute(select(cases.c.case_id).where(cases.c.group_ref == group)).scalar()
            if not cid:
                cid = f"CASE-{conn.execute(text('SELECT count(*) FROM cases')).scalar() + 201:04d}"
                conn.execute(cases.insert().values(case_id=cid, break_key=b["break_key"], group_ref=group, status="new",
                                                   opened_on=d, updated_on=d, amount_usd=0))
                emit(conn, "workflow", "case_opened", f"case for {group}", case_id=cid,
                     inputs={"break_id": b["break_id"]}, sim_date=d)
                n += 1
            conn.execute(breaks.update().where(breaks.c.break_id == b["break_id"]).values(case_id=cid))
            conn.execute(text("UPDATE cases SET amount_usd = amount_usd + :a WHERE case_id = :c"),
                         {"a": b["amount_usd"], "c": cid})
        return n

    def case_breaks(self, conn, cid: str) -> list[dict]:
        return [dict(r) for r in conn.execute(select(breaks).where(breaks.c.case_id == cid)
                                              .order_by(breaks.c.break_id)).mappings()]

    def _set(self, conn, case: dict, d: date, note: str | None = None, actor: str = "workflow", **vals) -> None:
        if note:
            notes = list(case.get("notes") or []) + [{"date": d.isoformat(), "actor": actor, "text": note}]
            vals["notes"] = notes
            case["notes"] = notes
        vals["updated_on"] = d
        conn.execute(cases.update().where(cases.c.case_id == case["case_id"]).values(**vals))
        if "status" in vals and vals["status"] != case.get("status"):
            emit(conn, actor, "case_status", f"{case.get('status')} -> {vals['status']}" + (f": {note}" if note else ""),
                 case_id=case["case_id"], inputs={"from": case.get("status"), "to": vals["status"]}, sim_date=d)
        case.update(vals)

    def step_safe(self, cid: str, d: date) -> bool:
        try:
            with self.eng.begin() as c:
                return self.step(c, cid, d)
        except Exception as e:  # a failed step never posts anything (transaction rolled back)
            with self.eng.begin() as c:
                case = dict(c.execute(select(cases).where(cases.c.case_id == cid)).mappings().one())
                self._set(c, case, d, status="needs_human", note=f"error in workflow: {type(e).__name__}: {e}")
                emit(c, "workflow", "error", traceback.format_exc()[-1500:], case_id=cid, sim_date=d)
            return True

    def step(self, conn, cid: str, d: date) -> bool:
        case = dict(conn.execute(select(cases).where(cases.c.case_id == cid)).mappings().one())
        brks = self.case_breaks(conn, cid)
        st = case["status"]
        if st == "new":
            self._set(conn, case, d, status="investigating", actor="investigator")
            r = self.investigator.investigate(conn, case, brks, d)
            if r["status"] == "needs_human":
                self._set(conn, case, d, status="needs_human", actor="investigator", note=r["reason"], facts=r["facts"],
                          playbook_id=r["playbook"].id if r.get("playbook") else None,
                          playbook_version=r["playbook"].version if r.get("playbook") else None)
                return True
            pb, br = r["playbook"], r["branch"]
            self._set(conn, case, d, actor="investigator", facts=r["facts"], playbook_id=pb.id,
                      playbook_version=pb.version, branch_id=br.id, pending_actions=r["actions"],
                      summary=r["explanation"],
                      note=f"matched {pb.id} v{pb.version} branch '{br.id}' (when {br.when}); trust {br.trust}. "
                           f"{r['explanation']}")
            emit(conn, "investigator", "root_cause", r["explanation"], case_id=cid,
                 inputs={"playbook": pb.id, "version": pb.version, "branch": br.id, "llm": r.get("backend")},
                 evidence=[{"break": b["break_id"], "detection_key": b["detection_key"]} for b in brks], sim_date=d)
            return True
        if st in ("investigating", "waiting", "awaiting_approval"):
            return self.advance(conn, case, brks, d)
        return False

    # ------------------------------------------------------------------ actions
    def advance(self, conn, case: dict, brks: list[dict], d: date) -> bool:
        progressed = False
        while case["pending_actions"]:
            name = case["pending_actions"][0]
            act = ACTIONS[name]
            done = False
            if act.type in ("journal_entry", "transfer"):
                done, moved = self._money_action(conn, case, brks, name, d)
                progressed |= moved
            elif act.type == "notify":
                self.outreach.send(conn, case, act.to_role, "notify", name.replace("_", " "),
                                   self._msg_facts(conn, case, brks, name), d, account_id=brks[0]["account_id"])
                done = True
            elif act.type in ("request_info", "hold"):
                req = self.outreach.open_request(conn, case["case_id"])
                if req is None or req["meta"].get("purpose") != name:
                    self.outreach.send(conn, case, act.to_role, "request", name, self._msg_facts(conn, case, brks, name),
                                       d, account_id=brks[0]["account_id"])
                    self._set(conn, case, d, status="waiting", waiting_on=self.outreach.recipient(
                        act.to_role, brks[0]["account_id"]), waiting_since=d, note=f"waiting on {act.to_role}: {name}")
                    return True
                rep = self.outreach.reply_for(conn, req["msg_id"])
                if rep is None:
                    if case["status"] != "waiting":
                        self._set(conn, case, d, status="waiting")
                    return progressed
                if not rep["meta"].get("confirms"):
                    self._set(conn, case, d, status="needs_human", note=f"{rep['from_party']} did not confirm: {rep['body']}")
                    return True
                self._set(conn, case, d, status="investigating", waiting_on=None,
                          note=f"{rep['from_party']} replied: {rep['body']}")
                done = True
            elif act.type == "escalate":
                self._set(conn, case, d, status="needs_human", note="playbook says escalate to the controller")
                return True
            if not done:
                return progressed
            self._set(conn, case, d, pending_actions=case["pending_actions"][1:])
            progressed = True
        return self.verify(conn, case, brks, d) or progressed

    def _money_action(self, conn, case, brks, name, d) -> tuple[bool, bool]:
        rows = [dict(r) for r in conn.execute(select(proposals).where(proposals.c.case_id == case["case_id"])
                                              .order_by(proposals.c.proposal_id)).mappings()
                if r.body["action_name"] == name]
        if not rows:
            pids = self.preparer.prepare(conn, name, case, brks, d)
            if not pids:
                return True, True  # nothing to post (calculator found nothing to correct)
            for pid in pids:
                if approvals.route(conn, pid, d) == "approver":
                    self.approver.review(conn, pid, d)
            rows = [approvals.load(conn, p) for p in pids]
        moved = False
        for r in rows:
            r = approvals.load(conn, r["proposal_id"])
            if r["status"] == "approved":
                self.executor.execute(conn, r["proposal_id"], d)
                moved = True
            elif r["status"] == "rejected":
                self._set(conn, case, d, status="needs_human", note=f"{r['proposal_id']} was rejected")
                return False, True
        statuses = {approvals.load(conn, r["proposal_id"])["status"] for r in rows}
        if statuses == {"executed"}:
            if case["status"] != "investigating":
                self._set(conn, case, d, status="investigating")
            return True, True
        if case["status"] != "awaiting_approval":
            self._set(conn, case, d, status="awaiting_approval", note="awaiting controller approval")
            return False, True
        return False, moved

    def _msg_facts(self, conn, case, brks, name) -> dict:
        b = brks[0]
        st = self.src("statements").statement(b["ref"])
        lease = self.src("leases").lease(st.lease_id)
        clause = {M.K_SHARE: "share", M.K_FEE: "admin_fee", M.K_CAP: "cap", M.K_CAPITAL: "exclusions", M.K_TAX: "taxes",
                  M.K_GROSS: "reconciliation", M.K_EST: "reconciliation"}.get(b["break_key"], "audit")
        sec = lease.clauses.get(clause) or lease.clauses["audit"]
        credit = sum((D(r.amount_usd) for r in conn.execute(select(proposals.c.amount_usd).where(
            proposals.c.case_id == case["case_id"], proposals.c.status == "executed"))), D(0))
        out = {"statement_id": st.statement_id, "lease_id": lease.lease_id, "store": lease.store,
               "landlord": self.src("reference").landlord(st.landlord_id).name, "year": st.year,
               "finding": b["reason"], "section": sec.section, "credit": s(credit),
               "audit_section": lease.clauses["audit"].section, "audit_window_days": lease.audit_window_days,
               "stores": len(brks)}
        if name == "request_tax_bills":
            center = self.src("reference").center(lease.center_id)
            out.update({"apn": center.apn, "county": center.county})
        if name == "send_payment_proof":
            out.update({"payments": len(self.src("ap").estimate_payments(lease.lease_id, st.year))})
        return out

    # ------------------------------------------------------------------ verify and completion
    def verify(self, conn, case: dict, brks: list[dict], d: date) -> bool:
        failing = [b["break_id"] for b in brks if self.m.still_broken(conn, b, d)]
        executed = [dict(r) for r in conn.execute(select(proposals.c.proposal_id, proposals.c.status).where(
            proposals.c.case_id == case["case_id"])).mappings()]
        pb = (case["playbook_id"], case["playbook_version"], case["branch_id"])
        if failing:
            if case["playbook_id"] and case["branch_id"]:
                pbstore.bump_stats(conn, *pb, succeeded=False)
                pbstore.set_status(conn, case["playbook_id"], case["playbook_version"], "flagged", "verify",
                                   f"verify failed on {case['case_id']}: {failing} still broken", d)
            self._set(conn, case, d, status="needs_human", note=f"verify failed: {failing} still broken after the fix")
            return True
        for b in brks:
            conn.execute(breaks.update().where(breaks.c.break_id == b["break_id"]).values(status="resolved", resolved_on=d))
        completion = {"verify": f"rerun recon on {len(brks)} key(s): 0 breaks",
                      "proposals": [p["proposal_id"] for p in executed if p["status"] == "executed"]}
        if case["playbook_id"] and case["branch_id"]:
            pbstore.bump_stats(conn, *pb, succeeded=True)
            p = pbstore.get(conn, case["playbook_id"], case["playbook_version"])
            br = next(b for b in p.branches if b.id == case["branch_id"])
            completion.update({"playbook": case["playbook_id"], "version": case["playbook_version"],
                               "branch": case["branch_id"], "provenance": br.provenance.model_dump()})
            note = (f"completed by {case['playbook_id']} v{case['playbook_version']} branch '{case['branch_id']}' "
                    f"(taught by {br.provenance.taught_by} on {br.provenance.from_case}, approved by "
                    f"{br.provenance.approved_by}); {completion['verify']}")
        else:
            ts = conn.execute(select(teach_sessions).where(teach_sessions.c.case_id == case["case_id"])).mappings().first()
            completion.update({"taught_by": ts["teacher"] if ts else None, "teach_session": ts["id"] if ts else None})
            note = f"completed by a taught one-off fix ({completion['taught_by']}); {completion['verify']}"
        self._set(conn, case, d, status="completed", closed_on=d, completion=jsonable(completion), note=note)
        return True

    # ------------------------------------------------------------------ teach mode (R21)
    def teach(self, conn, case_id: str, *, teacher: str, trusted_source: str, actions: list[str], fact: str,
              note: str, d: date) -> dict:
        case = dict(conn.execute(select(cases).where(cases.c.case_id == case_id)).mappings().one())
        if case["status"] != "needs_human":
            raise ValueError(f"{case_id} is {case['status']}, not needs_human")
        brks = self.case_breaks(conn, case_id)
        facts = case["facts"] or {}
        if not facts:
            from bp.recon.context import facts_for, to_jsonable
            facts = {b["break_id"]: to_jsonable(facts_for(conn, self.m, b, d)) for b in brks}
        tid = f"TEACH-{conn.execute(text('SELECT count(*) FROM teach_sessions')).scalar() + 1:03d}"
        hist, _ = load_history()
        pb = draft_from_teach(conn, case=case, break_facts=[facts[b["break_id"]] for b in brks], trusted=trusted_source,
                              actions=actions, fact=fact, teacher=teacher, d=d, history=hist)
        conn.execute(teach_sessions.insert().values(id=tid, case_id=case_id, teacher=teacher,
                                                    trusted_source=trusted_source, actions=actions, fact=fact,
                                                    note=note, created_on=d, draft_playbook=pb.id,
                                                    draft_version=pb.version))
        emit(conn, teacher, "teach_session", note, case_id=case_id,
             inputs={"trusted_source": trusted_source, "actions": actions, "fact": fact, "draft": f"{pb.id} v{pb.version}"},
             sim_date=d)
        # the taught fix runs once, through the controller (no active playbook covers it yet)
        self._set(conn, case, d, status="investigating", facts=facts, playbook_id=None, playbook_version=None,
                  branch_id=None, pending_actions=actions, escalated=False,
                  summary=note, note=f"taught by {teacher}: trust {trusted_source}, do {actions}, because {fact}. "
                                     f"Learner drafted {pb.id} v{pb.version}.", actor=teacher)
        return {"teach_session": tid, "draft": {"id": pb.id, "version": pb.version}}

    def approve_playbook(self, conn, pid: str, version: int, approver: str, d: date, reason: str) -> None:
        if approver != approvals.CONTROLLER:  # new playbooks need the controller (R17); the learner never activates
            emit(conn, approver, "approval_denied", "only the controller approves playbooks",
                 inputs={"playbook": pid, "version": version}, sim_date=d)
            raise approvals.PolicyViolation(f"{approver} may not approve playbooks")
        pb = pbstore.get(conn, pid, version)
        body = pb.dump()
        for b in body["branches"]:
            if not b["provenance"].get("approved_by"):
                b["provenance"]["approved_by"] = approver
        from bp.db import playbooks
        conn.execute(playbooks.update().where(playbooks.c.id == pid, playbooks.c.version == version).values(body=body))
        pbstore.set_status(conn, pid, version, "active", approver, reason, d, approved_by=approver)
        # cases that stopped for lack of this lesson get another look
        reopened = conn.execute(cases.update().where(
            cases.c.status == "needs_human", cases.c.break_key == pb.key.name(), cases.c.escalated.is_(False),
            cases.c.case_id.notin_(select(teach_sessions.c.case_id))).values(status="new", updated_on=d)
            .returning(cases.c.case_id)).scalars().all()
        if reopened:
            emit(conn, "workflow", "cases_reopened", f"{pid} v{version} is active; re-investigating {len(reopened)} case(s)",
                 inputs={"cases": reopened}, sim_date=d)

    def override(self, conn, case_id: str, by: str, note: str, d: date) -> None:
        """A human resolves a matched case differently: the playbook is flagged for revision (R22)."""
        case = dict(conn.execute(select(cases).where(cases.c.case_id == case_id)).mappings().one())
        if case["playbook_id"]:
            pbstore.set_status(conn, case["playbook_id"], case["playbook_version"], "flagged", by,
                               f"overridden on {case_id}: {note}", d)
        self._set(conn, case, d, status="needs_human", pending_actions=[], actor=by,
                  note=f"{by} overrode the playbook: {note}")

    # ------------------------------------------------------------------ scripted people (demo mode)
    def scripted_controller(self, conn, d: date) -> None:
        for p in approvals.pending_for_controller(conn):
            if p["created_on"] < d:
                approvals.decide(conn, p["proposal_id"], approvals.CONTROLLER, "approved",
                                 "reviewed evidence and calculator output (scripted controller)", d)
        from bp.db import playbooks
        for r in conn.execute(select(playbooks.c.id, playbooks.c.version, playbooks.c.created_on).where(
                playbooks.c.status == "draft")).all():
            if r.created_on and r.created_on < d:
                self.approve_playbook(conn, r.id, r.version, approvals.CONTROLLER, d,
                                      "reviewed the drafted branch and its test case (scripted controller)")

    def scripted_teacher(self, conn, d: date) -> bool:
        did = False
        for case in [dict(r) for r in conn.execute(select(cases).where(
                cases.c.status == "needs_human", cases.c.escalated.is_(False))).mappings()]:
            lesson = SCRIPTED_LESSONS.get(case["break_key"])
            taught = conn.execute(select(teach_sessions.c.id).where(teach_sessions.c.case_id == case["case_id"])).first()
            draft = pbstore.for_key(conn, case["break_key"], "draft")
            active = pbstore.for_key(conn, case["break_key"], "active")
            if not lesson or taught or draft or active:
                continue
            self.teach(conn, case["case_id"], teacher="Dana Ortiz", d=d, **lesson)
            did = True
        return did
