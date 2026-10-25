"""Investigator, Preparer and Approver agents.

Investigator (LLM): gathers context facts, looks the playbook up by break key (no
inference), evaluates branches, then asks the model to confirm the branch fits the
evidence and explain it. It may only write notes and root cause.

Preparer (LLM + calculators): turns a branch's journal-entry actions into proposals.
Every number comes from bp.calculators; the model only writes the memo.

Approver (LLM bound by coded policy): may approve only what the policy allows
(<= the agent limit, active playbook, no money movement). The model can decline an
eligible proposal (sending it to the controller) but can never widen the policy.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import text

from bp import calculators, config
from bp.agents import approvals, llm
from bp.agents.events import emit, jsonable
from bp.agents.tools import AgentTools
from bp.models import Proposal
from bp.money import D, s
from bp.playbooks import conditions
from bp.playbooks import store as pbstore
from bp.playbooks.library import ACTIONS
from bp.recon import context as ctx
from bp.recon import matcher as M


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
    m = lambda x: f"${D(x):,.2f}"  # noqa: E731
    if key == M.K_SHARE:
        return (f"The landlord billed a {s(f['billed_share_pct'])}% share, which is our square footage over the center's "
                f"occupied area ({s(f['share_by_occupied_pct'])}%). The lease fixes the share at {s(f['lease_share_pct'])}% "
                "of gross leasable area whether or not it is leased, so every line is overstated.")
    if key == M.K_FEE:
        return (f"The statement charges an admin fee of {m(f['billed_fee'])}. The lease caps it at {s(f['lease_fee_pct'])}% "
                f"of operating expenses after the controllable cap and outside its exclusions, which is {m(f['lease_max_fee'])}.")
    if key == M.K_CAP:
        return (f"Controllable expenses are billed at {m(f['controllable_billed'])}, above the lease cap of "
                f"{m(f['cap_limit'])} ({s(f['cap_pct'])}% over the prior year), and the statement has no cap adjustment.")
    if key == M.K_CAPITAL:
        return (f"{b['detection_key'].split(':')[-1]} is a replacement or structural item ({m(f['line_amount'])}). The lease "
                "excludes costs of a capital nature from operating expenses, so none of it is ours.")
    if key == M.K_TAX:
        return (f"Real estate taxes are billed at {m(f['billed_tax'])}; the county's bill for the parcel is "
                f"{m(f['county_tax'])}. The lease passes through only taxes actually levied and paid, so we book to the "
                "county figure and ask the landlord for the bills.")
    if key == M.K_EST:
        return (f"The landlord credited {m(f['estimates_credited'])} of estimates, but {m(f['estimates_cleared'])} cleared "
                f"our bank: one {m(f['monthly_estimate'])} monthly payment is missing from their ledger.")
    if key == M.K_GROSS:
        return (f"The landlord grossed up variable expenses to 95% occupancy ({m(f['gross_up_billed'])}) with the center "
                f"{s(f['occupancy_pct'])}% occupied. The lease has no gross-up clause, so the adjustment is not chargeable"
                + (f" at any of the {len(brks)} stores in this case." if len(brks) > 1 else "."))
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
        out = []
        for b in brks:
            f = self.m.finding(b)
            if f is None or f["calculator"] != tmpl:
                continue
            booked = conn.execute(text("SELECT txn_id FROM ledger_entries WHERE source_ref = :r AND kind = 'statement'"),
                                  {"r": b["ref"]}).scalars().all()
            out.append({"calculator": tmpl, "inputs": f["inputs"], "source_ref": b["detection_key"],
                        "references": list(booked)})
        if not out:
            raise ValueError(f"no finding on this case is priced by {tmpl}")
        return out

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
                parts = [f"{k} {s(v) if isinstance(v, Decimal) else v}" for k, v in out["info"].items()
                         if not isinstance(v, dict) and k != "credit"]
                return Memo(memo=f"{action_name} for {case['case_id']}: calculator {g['calculator']} on "
                                 f"{g['source_ref']} credits ${s(out['amount_usd'])} against the landlord payable"
                                 + (f" ({'; '.join(parts)})" if parts else "") + ".")
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
                f"Within policy (${s(D(row['amount_usd']))} <= ${config.APPROVAL_LIMIT_USD:,}, active playbook {body['playbook']['id']} "
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
