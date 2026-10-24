"""Auditor (LLM, optional): answers "why" questions from the event log only, with citations.

It can read events and nothing else. Every citation must be an event it was shown.
"""
from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import or_, select

from bp.agents import llm
from bp.agents.tools import AgentTools
from bp.db import events


class Answer(BaseModel):
    answer: str
    citations: list[int]


class Auditor:
    def __init__(self):
        self.tools = AgentTools("auditor")

    def events_for(self, conn, case_id: str) -> list[dict]:
        rows = conn.execute(select(events).where(events.c.case_id == case_id).order_by(events.c.seq)).mappings()
        evs = [dict(r) for r in rows]
        pbs = {e["inputs"].get("playbook") for e in evs if e["inputs"].get("playbook")}
        if pbs:
            extra = conn.execute(select(events).where(events.c.action.in_(["playbook_active", "playbook_saved",
                                                                          "teach_session", "playbook_drafted"]))
                                 .order_by(events.c.seq)).mappings()
            evs += [dict(r) for r in extra if r["inputs"].get("playbook") in pbs and r["case_id"] != case_id]
        return sorted(evs, key=lambda e: e["seq"])

    def ask(self, conn, question: str, case_id: str) -> dict:
        self.tools.check(conn, "read_events", case_id=case_id)
        evs = self.events_for(conn, case_id)
        shown = {e["seq"] for e in evs}

        def offline():
            keys = ("root_cause", "proposal_routed", "proposal_approved", "proposal_executed", "case_status",
                    "teach_session", "playbook_active", "reply_recorded")
            picked = [e for e in evs if e["action"] in keys]
            lines = [f"[{e['seq']}] {e['sim_date']} {e['actor']}: {e['action'].replace('_', ' ')} — {e['reason']}"
                     for e in picked]
            return Answer(answer=f"From the event log for {case_id}:\n" + "\n".join(lines),
                          citations=[e["seq"] for e in picked])

        payload = {"question": question, "case_id": case_id,
                   "events": [{k: e[k] for k in ("seq", "sim_date", "actor", "action", "reason", "inputs")} for e in evs]}
        ans, backend = llm.decide("auditor", "Answer the question using only these events. Cite the seq number of every "
                                  "event you rely on in `citations`, and put [seq] markers in the answer.", payload,
                                  Answer, offline)
        if not ans.citations or not set(ans.citations) <= shown:
            ans, backend = offline(), backend + " (citations invalid; used event summary)"
        return {"answer": ans.answer, "citations": ans.citations, "backend": backend}
