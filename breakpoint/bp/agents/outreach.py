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
NO_REPLY = {"LL-SVR"}  # this landlord never answers a request (shows escalation); mirrors bp.world.generate.NO_REPLY


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
        """account_id is the lease: a landlord message goes to that landlord's property manager."""
        if role == "landlord" and account_id:
            ll = self.src("reference").landlord(self.src("leases").lease(account_id).landlord_id)
            return f"{ll.contact} ({ll.name})"
        return self.src("directory").person(role).name

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
    if purpose == "dispute notice":
        stores = f" across {f.get('stores')} of our stores" if str(f.get("stores", "1")) != "1" else ""
        return (f"Re: {f.get('year')} CAM reconciliation {f.get('statement_id')} ({f.get('store')}). Under Section "
                f"{f.get('section')} of our lease we dispute this item{stores}: {f.get('finding')}. Our share of the "
                f"overcharge is ${f.get('credit')}; please apply it as a credit against the next rent due. This notice "
                f"is given within the {f.get('audit_window_days')}-day period in Section {f.get('audit_section')}.")
    if purpose == "request_tax_bills":
        return (f"Re: {f.get('statement_id')} ({f.get('store')}). The statement's real estate taxes exceed the "
                f"{f.get('county')} County bill for APN {f.get('apn')}. Under Section {f.get('section')} only taxes "
                "actually levied and paid pass through. Please send the 2025-26 tax bills and proof of payment.")
    if purpose == "send payment proof":
        return (f"Re: {f.get('statement_id')} ({f.get('store')}). Your statement credits fewer CAM estimate payments "
                f"than we made: all {f.get('payments')} monthly payments for {f.get('year')} cleared our bank "
                f"(confirmations attached). Please credit the missing ${f.get('credit')}.")
    if role == "accounts_payable":
        return (f"{ref}: please hold ${f.get('credit')} from the {f.get('landlord')} balance due on "
                f"{f.get('statement_id')} while the dispute is open.")
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
            if role == "landlord" and acct and self.src.store.lease(acct).landlord_id in NO_REPLY:
                continue
            if bd_between(req["sent_on"], d) < sla:
                continue
            truth = self._truth(req)

            def offline():
                return PersonaReply(confirms=truth["confirms"], body=truth["text"])
            rep, _ = llm.decide(f"persona {req['to_name']} ({role})",
                                "You are role-playing this person replying to Golden Wok's message. Answer only from the "
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
        if req["to_role"] == "landlord" and req["meta"].get("purpose") == "request_tax_bills":
            rec = st.tax_record(f.get("apn"))
            return {"confirms": True, "apn": f.get("apn"), "county_tax": s(rec.total_tax),
                    "text": f"Attached are the {rec.tax_year} tax bills for APN {rec.apn}: ${rec.total_tax:,} paid in two "
                            "installments. Our statement carried the prior estimate; we'll apply the difference as a credit."}
        return {"confirms": True, "text": "Acknowledged."}
