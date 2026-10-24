"""Investigator, Preparer and Approver agents.

Investigator (LLM): gathers context facts, looks the playbook up by break key (no
inference), evaluates branches, then asks the model to confirm the branch fits the
evidence and explain it. It may only write notes and root cause.

Preparer (LLM + calculators): turns a branch's journal-entry actions into proposals.
Every number comes from bp.calculators; the model only writes the memo.

Approver (LLM bound by coded policy): may approve only what the policy allows
(<= $500, active playbook, no client money movement). The model can decline an
eligible proposal (sending it to the controller) but can never widen the policy.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import text

from bp import calculators
from bp.agents import approvals, llm
from bp.agents.events import emit, jsonable
from bp.agents.tools import AgentTools
from bp.models import Proposal
from bp.money import D, cents, s
from bp.playbooks import conditions
from bp.playbooks import store as pbstore
from bp.playbooks.library import ACTIONS
from bp.recon import context as ctx
from bp.recon import matcher as M
from bp.world.generate import compute_fee


# ====================================================================== Investigator

class BranchCheck(BaseModel):
    fits: bool
    root_cause_source: str
    explanation: str


def match_branches(pb, facts: dict) -> list[str]:
    """Branches whose condition holds. Conditions see only the playbook's context facts (R19)."""
    visible = {k: v for k, v in facts.items() if k in pb.context}
    return [b.id for b in pb.branches if conditions.evaluate(b.when, visible)]


class Investigator:
    name = "investigator"

    def __init__(self, matcher: M.Matcher):
        self.m = matcher
        self.tools = AgentTools("investigator")

    def investigate(self, conn, case: dict, brks: list[dict], d: date) -> dict:
        self.tools.check(conn, "read_sources", d, case["case_id"])
        self.tools.check(conn, "match_playbook", d, case["case_id"])
        facts = {b["break_id"]: ctx.facts_for(conn, self.m, b, d) for b in brks}
        key = case["break_key"]
        pb = pbstore.for_key(conn, key, "active")
        res = {"facts": {k: ctx.to_jsonable(v) for k, v in facts.items()}}
        if pb is None:
            draft = pbstore.for_key(conn, key, "draft")
            why = (f"no active playbook for {key}" + (f"; draft {draft.id} v{draft.version} awaits the controller"
                                                       if draft else ""))
            return {**res, "status": "needs_human", "reason": why}
        matched_ids = set()
        per_break = {}
        for bid, f in facts.items():
            hits = match_branches(pb, f)
            per_break[bid] = hits
            matched_ids |= set(hits)
            if len(hits) > 1:
                return {**res, "status": "needs_human", "playbook": pb, "reason":
                        f"branches {hits} both match {bid}: their conditions overlap; escalating so a human can tighten one"}
            if not hits:
                return {**res, "status": "needs_human", "playbook": pb,
                        "reason": f"{pb.id} v{pb.version}: no branch fits {bid} (else -> needs human)"}
        if len(matched_ids) != 1:
            return {**res, "status": "needs_human", "playbook": pb,
                    "reason": f"breaks in this case take different branches {sorted(matched_ids)}"}
        branch = next(b for b in pb.branches if b.id in matched_ids)
        first = next(iter(facts.values()))
        visible = {k: v for k, v in first.items() if k in pb.context}

        def offline():
            return BranchCheck(fits=True, root_cause_source=branch.trust, explanation=_explain(key, branch, visible, brks))

        check, backend = llm.decide(
            "investigator",
            "A deterministic lookup chose this playbook branch. Confirm whether the branch fits the evidence, name the "
            "source to trust, and explain the root cause in two or three plain sentences for an ops analyst. "
            "Do not compute or restate amounts beyond the facts given.",
            {"break_key": key, "breaks": [{k: b[k] for k in ("detection_key", "vals", "reason")} for b in brks],
             "facts": visible, "branch": branch.model_dump(), "playbook": {"id": pb.id, "version": pb.version}},
            BranchCheck, offline)
        if not check.fits:
            return {**res, "status": "needs_human", "playbook": pb, "branch": branch,
                    "reason": f"investigator does not think {branch.id} fits: {check.explanation}"}
        actions = list(branch.actions)
        for mod in pb.modifiers:
            if conditions.evaluate(mod.when, visible):
                actions = [a for a in actions if a not in mod.skip]
        return {**res, "status": "matched", "playbook": pb, "branch": branch, "actions": actions,
                "root_cause": check.root_cause_source, "explanation": check.explanation, "backend": backend}


def _explain(key, branch, f, brks) -> str:
    b = brks[0]
    if branch.id == "partial-fill":
        return (f"The broker filled {s(f['executions_qty'])} of {s(f['order_qty'])} shares and the custodian settled "
                f"{s(f['custodian_qty'])}, but the ledger booked the full {s(f['ledger_qty'])}. Executions are right; "
                f"the booking system booked the order quantity instead of the fills.")
    if branch.id == "duplicate-report":
        return ("The broker re-sent the execution report (PossDup) and the booking system posted it twice; the "
                f"custodian settled once ({s(f['custodian_qty'])} shares). The second booking is a duplicate.")
    if branch.id == "stale-schedule":
        return (f"The commission charged ({s(f['ledger_fee'])}) equals the previous schedule version, but schedule "
                f"v{s(f['schedule_version'])} ({s(f['schedule_fee'])}) was in force on the trade date. Refund the difference.")
    if branch.id == "dividend-missing":
        return (f"The custodian credited a dividend of {s(f['dividend_amount'])} and the ledger is short by exactly that "
                "amount. The corporate-action job missed this account.")
    if branch.id == "split":
        return (f"A {s(f['split_ratio'])}:1 split took effect today. The custodian applied it ({s(f['custodian_qty'])} "
                f"shares); the ledger still shows {s(f['ledger_qty'])}. Applies to every holder in this case "
                f"({len(brks)}). Cash is unaffected; cost per share divides by the ratio.")
    if branch.id == "ach-return":
        return ("The bank returned the client's ACH deposit (R01) after it had been spent. Reverse the deposit, book "
                "what the client now owes as a receivable, and ask the client for funds.")
    return f"{branch.id}: trusted source {branch.trust}; condition {branch.when} holds. {b.get('reason') or ''}"


# ====================================================================== Preparer

class Memo(BaseModel):
    memo: str


class Preparer:
    name = "preparer-agent"

    def __init__(self, matcher: M.Matcher):
        self.m = matcher
        self.tools = AgentTools("preparer")

    def gather(self, conn, action_name: str, case: dict, brks: list[dict], d: date) -> list[dict]:
        """Return [{calculator, inputs, source_ref, references}] for one action (deterministic reads only)."""
        tmpl = ACTIONS[action_name].template
        b = brks[0]
        if tmpl == "reverse_unfilled":
            tv = self.m.trade_view(conn, b["ref"])
            o, lv = tv["order"], tv["ledger"]
            fills, seen = [], set()
            for e in self.m.src("executions").executions_for(o.order_id):
                if e.exec_type != "cancel" and e.exec_id not in seen and not e.poss_dup:
                    seen.add(e.exec_id)
                    fills.append(e)
            exec_qty = sum((e.fill_qty for e in fills), D(0))
            fees = self.m.src("fees")
            booking = next(e["txn_id"] for e in tv["entries"] if e["kind"] == "trade")
            return [{"calculator": "reverse_unfilled", "source_ref": o.order_id, "references": [booking], "inputs": {
                "account_id": o.account_id, "symbol": o.symbol, "side": o.side, "ledger_qty": lv["qty"],
                "ledger_principal": lv["principal"], "ledger_fee": lv["fee"], "exec_qty": exec_qty,
                "exec_principal": sum((cents(e.fill_qty * e.fill_px) for e in fills), D(0)),
                "schedule_fee": compute_fee(fees.tier[o.account_id], exec_qty, fees.fee_schedule_on(o.trade_date))}}]
        if tmpl == "reverse":
            tv = self.m.trade_view(conn, b["ref"])
            dup = [e for e in tv["entries"] if e["kind"] == "trade"][-1]
            return [{"calculator": "reverse", "source_ref": b["ref"], "references": [dup["txn_id"]],
                     "inputs": {"txn_id": dup["txn_id"], "postings": dup["postings"]}}]
        if tmpl == "adjust":
            fv = self.m.fee_view(conn, b["ref"])
            return [{"calculator": "adjust", "source_ref": b["ref"],
                     "references": [x["txn_id"] for x in fv["bookings"]],
                     "inputs": {"account_id": b["account_id"], "charged": fv["net_charged"],
                                "schedule": fv["net_schedule"], "schedule_version": fv["version"]}}]
        if tmpl == "dividend":
            det = _d(b["detected_on"])
            cust = self.m.src("custodian")
            out = []
            booked = ledger_refs(conn, [a.ref for a in cust.custodian_cash_activity(det)])
            for a in cust.custodian_cash_activity(det):
                if a.account_id != b["account_id"] or a.kind != "dividend" or a.ref in booked:
                    continue
                ca = next(c for c in self.m.src("reference").corporate_actions_asof(det)
                          if c.symbol == a.symbol and c.type == "dividend" and c.pay_date == det)
                shares = cust.custodian_positions(ca.record_date).get((a.account_id, a.symbol), D(0))
                out.append({"calculator": "dividend", "source_ref": f"{ca.action_id}:{a.account_id}", "references": [],
                            "inputs": {"account_id": a.account_id, "symbol": a.symbol, "rate": ca.rate,
                                       "shares": shares, "record_date": ca.record_date.isoformat(),
                                       "action_id": ca.action_id}})
            return out
        if tmpl == "split_adjust":
            det = _d(b["detected_on"])
            ca = next(c for c in self.m.src("reference").corporate_actions_asof(det)
                      if c.symbol == b["symbol"] and c.type == "split" and c.ex_date == det)
            holders = [{"account_id": x["account_id"], "qty": D(x["vals"]["ledger"]),
                        "avg_cost": _avg_cost(conn, x["account_id"], x["symbol"])} for x in brks]
            return [{"calculator": "split_adjust", "source_ref": ca.action_id, "references": [],
                     "inputs": {"symbol": ca.symbol, "ratio": ca.ratio, "action_id": ca.action_id,
                                "ex_date": ca.ex_date.isoformat(), "holders": holders}}]
        if tmpl == "reverse_deposit":
            bv = self.m.bank_view(conn, b["ref"], d)
            t = bv["transfer"]
            return [{"calculator": "reverse_deposit", "source_ref": t.transfer_id,
                     "references": [e["txn_id"] for e in bv["entries"]],
                     "inputs": {"account_id": t.account_id, "amount": t.amount, "transfer_id": t.transfer_id,
                                "return_code": t.return_code}}]
        if tmpl == "receivable":
            acct = b["account_id"]
            cash = conn.execute(text("SELECT coalesce(sum(amount), 0) FROM ledger_lines WHERE account = :a AND asset = 'USD'"),
                                {"a": f"client:{acct}"}).scalar()
            return [{"calculator": "receivable", "source_ref": f"{b['ref']}:receivable", "references": [],
                     "inputs": {"account_id": acct, "cash_after_reversal": cash, "transfer_id": b["ref"]}}]
        raise ValueError(f"no gatherer for template {tmpl}")

    def prepare(self, conn, action_name: str, case: dict, brks: list[dict], d: date) -> list[str]:
        self.tools.check(conn, "run_calculator", d, case["case_id"])
        self.tools.check(conn, "create_proposal", d, case["case_id"])
        pids = []
        pb = ({"id": case["playbook_id"], "version": case["playbook_version"], "branch": case["branch_id"]}
              if case.get("playbook_id") else None)
        for g in self.gather(conn, action_name, case, brks, d):
            out = calculators.run(g["calculator"], g["inputs"])
            if not out["postings"]:
                continue

            def offline():
                return Memo(memo=f"{action_name} for {case['case_id']}: calculator {g['calculator']} on "
                                 f"{g['source_ref']}; moves ${s(out['amount_usd'])} of client cash"
                                 + (f" ({', '.join(f'{k} {s(v) if isinstance(v, Decimal) else v}' for k, v in out['info'].items() if not isinstance(v, dict))})"
                                    if out["info"] else "") + ".")
            memo, _ = llm.decide("preparer", "Write a one-paragraph memo for the approver describing this correcting "
                                 "entry and why. Cite the calculator by name; do not restate or alter any amount.",
                                 {"case": case["case_id"], "action": action_name, "calculator": g["calculator"],
                                  "calculator_output": out, "evidence_refs": g["references"]}, Memo, offline)
            p = Proposal(
                proposal_id=approvals.next_proposal_id(conn), case_id=case["case_id"], action_name=action_name,
                action=ACTIONS[action_name], postings=out["postings"], amount_usd=out["amount_usd"],
                calculator=g["calculator"], calc_inputs=jsonable(g["inputs"]), references=g["references"],
                source_ref=g["source_ref"], playbook=pb, memo=memo.memo, prepared_by=self.name)
            pids.append(approvals.create(conn, p, d))
        return pids


def ledger_refs(conn, refs: list[str]) -> set[str]:
    if not refs:
        return set()
    return set(conn.execute(text("SELECT DISTINCT source_ref FROM ledger_entries WHERE source_ref = ANY(:r)"),
                            {"r": refs}).scalars())


def _d(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x))


def _avg_cost(conn, account_id: str, symbol: str) -> Decimal:
    r = conn.execute(text("""
        SELECT coalesce(sum(CASE WHEN l.asset = :s AND l.amount > 0 THEN l.amount END), 0) AS qty,
               coalesce(sum(CASE WHEN l.asset = 'USD' THEN l.amount END), 0) AS usd
        FROM ledger_entries e JOIN ledger_lines l ON l.txn_id = e.txn_id
        WHERE e.kind = 'trade' AND l.account = :a AND e.txn_id IN (
            SELECT e2.txn_id FROM ledger_entries e2 JOIN ledger_lines l2 ON l2.txn_id = e2.txn_id
            WHERE e2.kind = 'trade' AND l2.account = :a AND l2.asset = :s AND l2.amount > 0)"""),
                     {"a": f"client:{account_id}", "s": symbol}).first()
    if not r or not r.qty:
        return D(0)
    return cents(-D(r.usd) / D(r.qty))


# ====================================================================== Approver agent

class Review(BaseModel):
    approve: bool
    reason: str


class ApproverAgent:
    name = approvals.APPROVER_AGENT

    def __init__(self):
        self.tools = AgentTools("approver")

    def review(self, conn, pid: str, d: date) -> bool:
        self.tools.check(conn, "read_proposal", d)
        row = approvals.load(conn, pid)
        ok, why = approvals.agent_eligible(conn, row)
        if not ok:  # policy first: the model never sees what it may not approve
            approvals.route(conn, pid, d)
            return False
        body = row["body"]
        try:
            calculators.check(body["calculator"], body["calc_inputs"], body["postings"], D(body["amount_usd"]))
            calc_ok = True
        except calculators.CalculatorMismatch:
            calc_ok = False

        def offline():
            return Review(approve=calc_ok, reason=(
                f"Within policy (${s(D(row['amount_usd']))} <= $500, active playbook {body['playbook']['id']} "
                f"v{body['playbook']['version']} branch {body['playbook']['branch']}); calculator {body['calculator']} "
                f"reproduces every amount." if calc_ok else "calculator does not reproduce the amounts"))
        rev, _ = llm.decide("approver", "Review this proposal against its evidence. Approve only if the entry is what "
                            "the playbook branch prescribes and the calculator inputs match the evidence. If anything "
                            "looks off, decline and say why; it will go to the controller.",
                            {"proposal": body, "calculator_reproduces": calc_ok}, Review, offline)
        self.tools.check(conn, "approve_proposal" if rev.approve and calc_ok else "reject_proposal", d)
        if rev.approve and calc_ok:
            approvals.decide(conn, pid, self.name, "approved", rev.reason, d)
            return True
        conn.execute(text("UPDATE proposals SET route = 'controller' WHERE proposal_id = :p"), {"p": pid})
        emit(conn, self.name, "proposal_declined_to_controller", rev.reason, case_id=row["case_id"],
             inputs={"proposal_id": pid}, sim_date=d)
        return False
