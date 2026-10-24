"""Proposals and approvals (R16, R17).

A proposal's amounts must match its calculator. No one approves their own
proposal. An approval stores the SHA-256 of the proposal it approved. The approver
agent may only approve proposals of $500 or less from active playbooks that move no
client money; everything else goes to the controller.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date

from sqlalchemy import select, text

from bp import calculators, config
from bp.agents.events import emit, jsonable
from bp.db import approvals, playbooks, proposals
from bp.models import Proposal
from bp.money import D
from bp.money import s as dstr

APPROVER_AGENT = "approver-agent"
CONTROLLER = "Sam Lee"


class SelfApproval(PermissionError):
    pass


class PolicyViolation(PermissionError):
    pass


def proposal_hash(body: dict) -> str:
    return hashlib.sha256(json.dumps(jsonable(body), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def next_proposal_id(conn) -> str:
    return f"PRP-{conn.execute(text('SELECT count(*) FROM proposals')).scalar() + 1:05d}"


def create(conn, p: Proposal, d: date) -> str:
    calculators.check(p.calculator, p.calc_inputs, [x.model_dump() for x in p.postings], p.amount_usd)
    body = p.model_dump(mode="json")
    conn.execute(proposals.insert().values(
        proposal_id=p.proposal_id, case_id=p.case_id, body=body, hash=proposal_hash(body), amount_usd=p.amount_usd,
        prepared_by=p.prepared_by, status="pending", created_on=d))
    emit(conn, p.prepared_by, "proposal_created", p.memo, case_id=p.case_id,
         inputs={"proposal_id": p.proposal_id, "calculator": p.calculator, "amount_usd": p.amount_usd},
         evidence=[{"calculator": p.calculator, "inputs": p.calc_inputs}], sim_date=d)
    return p.proposal_id


def load(conn, pid: str) -> dict:
    return dict(conn.execute(select(proposals).where(proposals.c.proposal_id == pid)).mappings().one())


def playbook_active(conn, pb: dict | None) -> bool:
    if not pb:
        return False
    st = conn.execute(select(playbooks.c.status).where(
        playbooks.c.id == pb["id"], playbooks.c.version == pb["version"])).scalar()
    return st == "active"


def agent_eligible(conn, row: dict) -> tuple[bool, str]:
    body = row["body"]
    if D(row["amount_usd"]) > config.APPROVAL_LIMIT_USD:
        return False, f"amount ${dstr(D(row['amount_usd']))} is over the ${config.APPROVAL_LIMIT_USD} agent limit"
    if body["action"]["type"] == "transfer":
        return False, "client money movement needs the controller"
    if not playbook_active(conn, body.get("playbook")):
        return False, "not from an active playbook"
    return True, "within agent policy"


def route(conn, pid: str, d: date) -> str:
    row = load(conn, pid)
    ok, why = agent_eligible(conn, row)
    r = "approver" if ok else "controller"
    conn.execute(proposals.update().where(proposals.c.proposal_id == pid).values(route=r))
    emit(conn, "approval-policy", "proposal_routed", f"routed to {r}: {why}", case_id=row["case_id"],
         inputs={"proposal_id": pid}, sim_date=d)
    return r


def decide(conn, pid: str, decided_by: str, decision: str, reason: str, d: date) -> None:
    row = load(conn, pid)
    if decided_by == row["prepared_by"]:
        emit(conn, decided_by, "approval_denied", "preparer cannot approve its own proposal", case_id=row["case_id"],
             inputs={"proposal_id": pid}, sim_date=d)
        raise SelfApproval(f"{decided_by} prepared {pid} and cannot approve it")
    if row["status"] != "pending":
        raise PolicyViolation(f"{pid} is {row['status']}, not pending")
    if decided_by == APPROVER_AGENT and decision == "approved":
        ok, why = agent_eligible(conn, row)
        if not ok:
            emit(conn, decided_by, "approval_denied", why, case_id=row["case_id"], inputs={"proposal_id": pid}, sim_date=d)
            raise PolicyViolation(why)
    h = proposal_hash(row["body"])
    conn.execute(approvals.insert().values(proposal_id=pid, proposal_hash=h, decided_by=decided_by, decision=decision,
                                           reason=reason, decided_on=d))
    conn.execute(proposals.update().where(proposals.c.proposal_id == pid).values(status=decision))
    emit(conn, decided_by, f"proposal_{decision}", reason, case_id=row["case_id"],
         inputs={"proposal_id": pid, "hash": h}, sim_date=d)


def pending_for_controller(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(select(proposals).where(
        proposals.c.status == "pending", proposals.c.route == "controller").order_by(proposals.c.proposal_id)).mappings()]
