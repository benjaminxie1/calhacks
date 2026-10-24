"""Outreach (LLM) and the people it talks to.

Outreach routes by directory role (R18), sends notices and requests, follows up
after 1 business day without a reply and escalates to the controller after 3. It
cannot propose, post or move money.

Personas are LLM-played (template-played offline) and answer from the true world,
which no agent can read. Each has a reply SLA in business days.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel
from sqlalchemy import select, text

from bp.agents import llm
from bp.agents.events import emit
from bp.agents.tools import AgentTools
from bp.calendar import bd_between
from bp.db import messages
from bp.money import D, s

ACTOR = "outreach"
NO_REPLY = {"ACC-0052"}  # this client never answers the cash call (shows escalation)


class Draft(BaseModel):
    body: str


class PersonaReply(BaseModel):
    confirms: bool
    body: str


class Outreach:
    def __init__(self, sources):
        self.src = sources
        self.tools = AgentTools("outreach")

    def _next_id(self, conn) -> str:
        return f"MSG-{conn.execute(text('SELECT count(*) FROM messages')).scalar() + 1:05d}"

    def recipient(self, role: str, account_id: str | None = None) -> str:
        p = self.src("directory").person(role)
        return f"{account_id} holder" if role == "client" and account_id else p.name

    def send(self, conn, case: dict, role: str, kind: str, purpose: str, facts: dict, d: date,
             account_id: str | None = None, reply_to: str | None = None) -> str:
        self.tools.check(conn, "read_directory", d, case["case_id"])
        self.tools.check(conn, "send_message" if kind != "follow_up" else "follow_up", d, case["case_id"])
        to = self.recipient(role, account_id)

        def offline():
            return Draft(body=_template(kind, role, purpose, facts, case))
        draft, _ = llm.decide("outreach", f"Draft a short, specific {kind} message to {to} ({role}). Purpose: {purpose}. "
                              "Plain business English, 2-4 sentences, include the reference ids. No amounts that are "
                              "not in the facts.", {"case": case["case_id"], "facts": facts}, Draft, offline)
        mid = self._next_id(conn)
        conn.execute(messages.insert().values(
            msg_id=mid, case_id=case["case_id"], from_party="Breakpoint (outreach agent)", to_role=role, to_name=to,
            kind=kind, body=draft.body, sent_on=d, reply_to=reply_to,
            meta={"purpose": purpose, "account_id": account_id, "facts": {k: str(v) for k, v in facts.items()}}))
        emit(conn, ACTOR, f"message_{kind}", f"{kind} to {to}: {purpose}", case_id=case["case_id"],
             inputs={"msg_id": mid, "to_role": role}, sim_date=d)
        return mid

    def open_request(self, conn, case_id: str) -> dict | None:
        r = conn.execute(select(messages).where(messages.c.case_id == case_id, messages.c.kind == "request")
                         .order_by(messages.c.msg_id.desc())).mappings().first()
        return dict(r) if r else None

    def reply_for(self, conn, msg_id: str) -> dict | None:
        r = conn.execute(select(messages).where(messages.c.reply_to == msg_id, messages.c.kind == "reply")).mappings().first()
        return dict(r) if r else None

    def aging(self, conn, case: dict, d: date) -> str | None:
        """Follow up after 1 business day without a reply; escalate to the controller after 3."""
        req = self.open_request(conn, case["case_id"])
        if not req or req["answered"]:
            return None
        age = bd_between(req["sent_on"], d)
        sent = {r.kind for r in conn.execute(select(messages.c.kind).where(messages.c.reply_to == req["msg_id"]))}
        if age >= 3 and "escalation" not in sent:
            self.send(conn, case, "controller", "escalation", f"no reply from {req['to_name']} after {age} business days",
                      {"original_request": req["msg_id"], "to": req["to_name"]}, d, reply_to=req["msg_id"])
            return "escalated"
        if age >= 1 and "follow_up" not in sent:
            self.send(conn, case, req["to_role"], "follow_up", f"following up on {req['msg_id']}",
                      {"original_request": req["msg_id"]}, d, account_id=req["meta"].get("account_id"),
                      reply_to=req["msg_id"])
            return "followed_up"
        return None


def _template(kind, role, purpose, f, case) -> str:
    ref = case["case_id"]
    if kind == "follow_up":
        return f"Following up on our request ({f.get('original_request')}) for {ref}. Could you reply today? Thank you."
    if kind == "escalation":
        return (f"Escalating {ref}: {f.get('to')} has not replied to {f.get('original_request')} after 3 business days. "
                "This needs a controller decision.")
    if role == "client_service":
        return (f"{ref}: order {f.get('order_id')} for {f.get('account_id')} only filled {f.get('executions_qty')} of "
                f"{f.get('order_qty')} shares. The booking has been corrected; please send the client a corrected confirm.")
    if role == "broker_desk":
        return (f"{ref}: we received execution report {f.get('exec_id')} for order {f.get('order_id')} twice "
                "(second copy flagged PossDup). Please confirm it was a re-send and there was only one fill.")
    if role == "client" and purpose.startswith("fee"):
        return (f"We refunded ${f.get('refund')} of commission on order {f.get('order_id')}: the trade was charged under "
                f"the previous fee schedule. It will appear on your next statement.")
    if role == "client":
        return (f"Your ACH deposit {f.get('transfer_id')} of ${f.get('amount')} was returned by your bank "
                f"({f.get('return_code')}). Your account now owes ${f.get('owed')}. Please send funds within 3 business days.")
    if role == "custodian_ops":
        return f"{ref}: please confirm the position and settlement details for {f.get('account_id')} {f.get('symbol')}."
    return f"{ref}: {purpose}"


class Personas:
    """LLM-played people. They read the true world; agents never can."""

    def __init__(self, sources):
        self.src = sources

    def respond(self, conn, d: date) -> int:
        n = 0
        rows = conn.execute(select(messages).where(messages.c.kind == "request", messages.c.answered.is_(False))).mappings()
        for req in [dict(r) for r in rows]:
            role = req["to_role"]
            sla = self.src.store.person(role).reply_sla_days
            acct = req["meta"].get("account_id")
            if role == "client" and acct in NO_REPLY:
                continue
            if bd_between(req["sent_on"], d) < sla:
                continue
            truth = self._truth(req)

            def offline():
                return PersonaReply(confirms=truth["confirms"], body=truth["text"])
            rep, _ = llm.decide(f"persona {req['to_name']} ({role})",
                                "You are role-playing this person replying to Meridian's message. Answer only from the "
                                "true-world facts provided. Be brief and businesslike.",
                                {"message": req["body"], "true_world": truth}, PersonaReply, offline)
            mid = f"MSG-{conn.execute(text('SELECT count(*) FROM messages')).scalar() + 1:05d}"
            conn.execute(messages.insert().values(
                msg_id=mid, case_id=req["case_id"], from_party=req["to_name"], to_role="breakpoint",
                to_name="Breakpoint", kind="reply", body=rep.body, sent_on=d, reply_to=req["msg_id"],
                meta={"confirms": rep.confirms}))
            conn.execute(messages.update().where(messages.c.msg_id == req["msg_id"]).values(answered=True))
            emit(conn, "outreach", "reply_recorded", f"{req['to_name']} replied", case_id=req["case_id"],
                 inputs={"msg_id": mid, "confirms": rep.confirms}, sim_date=d)
            n += 1
        return n

    def _truth(self, req: dict) -> dict:
        f = req["meta"].get("facts", {})
        st = self.src.store
        if req["to_role"] == "broker_desk":
            oid = f.get("order_id")
            fills = {e.exec_id: e for e in st.executions_for(oid) if e.exec_type == "new"}
            qty = sum((e.fill_qty for e in fills.values()), D(0))
            return {"confirms": True, "order_id": oid, "unique_fills": len(fills), "filled_qty": s(qty),
                    "text": f"Confirmed. {f.get('exec_id')} was a re-send (PossDupFlag=Y). Order {oid} had "
                            f"{len(fills)} fill for {s(qty)} shares. Please reverse the duplicate."}
        if req["to_role"] == "client":
            return {"confirms": True, "text": f"Sorry about that. I've sent ${f.get('owed')} by wire today."}
        if req["to_role"] == "custodian_ops":
            return {"confirms": True, "text": "Confirmed: our statement reflects the settled position as reported."}
        return {"confirms": True, "text": "Acknowledged."}
