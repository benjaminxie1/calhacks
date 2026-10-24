"""Executor (deterministic code): the only component that writes to the ledger or bank.

It executes approved proposals only, refuses anything whose content changed after
approval (hash mismatch), re-checks the calculator, and posts with an idempotency
key so executing the same proposal twice yields one posting.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from bp import calculators
from bp.agents.approvals import load, proposal_hash
from bp.agents.events import emit
from bp.db import approvals, proposals
from bp.ledger import service as ledger
from bp.money import D

ACTOR = "executor"


class NotApproved(PermissionError):
    pass


class TamperedProposal(PermissionError):
    pass


class Executor:
    def __init__(self):
        self._cred = ledger.issue_credential("executor")

    def execute(self, conn, pid: str, d: date) -> list[str]:
        row = load(conn, pid)
        appr = conn.execute(select(approvals).where(approvals.c.proposal_id == pid, approvals.c.decision == "approved")
                            .order_by(approvals.c.id.desc())).mappings().first()
        if not appr:
            emit(conn, ACTOR, "execution_refused", "no approval on record", case_id=row["case_id"],
                 inputs={"proposal_id": pid}, sim_date=d)
            raise NotApproved(f"{pid} is not approved")
        body = row["body"]
        if proposal_hash(body) != appr["proposal_hash"]:
            emit(conn, ACTOR, "execution_refused", "proposal changed after approval (hash mismatch)",
                 case_id=row["case_id"], inputs={"proposal_id": pid, "approved_hash": appr["proposal_hash"],
                                                 "current_hash": proposal_hash(body)}, sim_date=d)
            raise TamperedProposal(f"{pid} changed after approval")
        calculators.check(body["calculator"], body["calc_inputs"], body["postings"], D(body["amount_usd"]))
        out = []
        act = body["action"]
        if act["type"] == "journal_entry":
            txn, created = ledger.post(
                conn, self._cred, txn_id=f"FIX-{pid}", idempotency_key=f"proposal:{pid}", effective_date=d,
                settle_date=d, kind="correction", source_ref=body["source_ref"],
                reason=f"{act['template']}: {body['memo'][:200]}", actor=ACTOR, postings=body["postings"],
                references_txn=(body.get("references") or [None])[0], proposal_id=pid,
                meta={"template": act["template"], "case_id": row["case_id"]})
            out.append(txn)
        elif act["type"] == "transfer":
            t = body["transfer"]
            tid, created = ledger.send_transfer(
                conn, self._cred, transfer_id=f"OUT-{pid}", idempotency_key=f"proposal:{pid}",
                account_id=t["account_id"], direction=t["direction"], amount=D(t["amount"]), sent_on=d, proposal_id=pid)
            out.append(tid)
        else:
            raise NotApproved(f"{act['type']} is not an executable action")
        conn.execute(proposals.update().where(proposals.c.proposal_id == pid).values(status="executed"))
        emit(conn, ACTOR, "proposal_executed", f"posted {', '.join(out)}" + ("" if created else " (idempotent replay)"),
             case_id=row["case_id"], inputs={"proposal_id": pid, "hash": appr["proposal_hash"]},
             evidence=[{"ledger_txn": x} for x in out], sim_date=d)
        return out
