"""Agents and segregation: T11, T16-T18, T20-T22."""
from datetime import date

import pytest
from sqlalchemy import select, text

from bp import calculators
from bp.agents import approvals
from bp.agents.executor import Executor, TamperedProposal
from bp.agents.tools import SCOPES, ALL_TOOLS, AgentTools, ToolDenied
from bp.db import events, messages, proposals
from bp.models import Action, Posting, Proposal
from bp.money import D

DAY = date(2026, 7, 6)


def _proposal(pid, amount="120", action="fee_refund", calc="adjust", playbook=None, tamper_amount=None):
    inputs = {"account_id": "ACC-0001", "charged": str(D(amount) + D("1")), "schedule": "1", "schedule_version": 2}
    out = calculators.run(calc, inputs)
    postings = [Posting(**p) for p in out["postings"]]
    amt = out["amount_usd"] if tamper_amount is None else D(tamper_amount)
    return Proposal(proposal_id=pid, case_id="CASE-T", action_name=action,
                    action=Action(type="journal_entry", template=calc), postings=postings, amount_usd=amt,
                    calculator=calc, calc_inputs=inputs, source_ref="ORD-T", playbook=playbook, memo="test",
                    prepared_by="preparer-agent")


def test_T11_calculator_only_amounts(db):
    """R11: a proposal whose amounts were not produced by its calculator is rejected."""
    with db.begin() as c:
        approvals.create(c, _proposal("PRP-OK"), DAY)
    with pytest.raises(calculators.CalculatorMismatch):
        with db.begin() as c:
            approvals.create(c, _proposal("PRP-BAD", tamper_amount="999"), DAY)
    bad = _proposal("PRP-BAD2")
    bad.postings[0] = Posting(account=bad.postings[0].account, asset="USD", amount=D("5000"))
    bad.postings[1] = Posting(account=bad.postings[1].account, asset="USD", amount=D("-5000"))
    with pytest.raises(calculators.CalculatorMismatch):
        with db.begin() as c:
            approvals.create(c, bad, DAY)
    with pytest.raises(calculators.CalculatorMismatch, match="no calculator"):
        calculators.check("llm_guess", {}, [], D(0))


def test_T16_tool_scoping(db):
    """R15: each agent calling a tool outside its scope is denied and logged."""
    with db.begin() as c:
        for agent, scope in SCOPES.items():
            outside = sorted(ALL_TOOLS - scope)[0]
            with pytest.raises(ToolDenied):
                AgentTools(agent).check(c, outside, DAY)
            AgentTools(agent).check(c, sorted(scope)[0], DAY)  # in scope: fine
        with pytest.raises(ToolDenied):
            AgentTools("investigator").check(c, "create_proposal", DAY)
        with pytest.raises(ToolDenied):
            AgentTools("outreach").check(c, "approve_proposal", DAY)
    with db.connect() as c:
        denied = c.execute(select(events).where(events.c.action == "tool_denied")).mappings().all()
    assert len(denied) == len(SCOPES) + 2
    assert all(e["actor"] and e["reason"] and e["inputs"]["tool"] for e in denied)


def test_T17_self_approval(db):
    """R16: a preparer approving its own proposal is rejected."""
    with db.begin() as c:
        approvals.create(c, _proposal("PRP-S"), DAY)
    with pytest.raises(approvals.SelfApproval):
        with db.begin() as c:
            approvals.decide(c, "PRP-S", "preparer-agent", "approved", "mine", DAY)
    with db.connect() as c:
        assert c.execute(text("SELECT count(*) FROM approvals")).scalar() == 0


def test_T18_tampered_proposal(db):
    """R16: editing a proposal after approval makes the executor refuse it."""
    with db.begin() as c:
        approvals.create(c, _proposal("PRP-X"), DAY)
        approvals.decide(c, "PRP-X", approvals.CONTROLLER, "approved", "ok", DAY)
        body = approvals.load(c, "PRP-X")["body"]
        body["memo"] = "changed after approval"
        c.execute(proposals.update().where(proposals.c.proposal_id == "PRP-X").values(body=body))
    with pytest.raises(TamperedProposal):
        with db.begin() as c:
            Executor().execute(c, "PRP-X", DAY)
    with db.connect() as c:
        assert c.execute(text("SELECT count(*) FROM ledger_entries")).scalar() == 0


def test_T20_approval_limit(db):
    """R17: a $501 proposal, or a new playbook, sent to the approver agent goes to the controller."""
    from bp.engine.workflow import Workflow
    from bp.playbooks import store as pbstore
    from bp.agents.learner import induct, load_history

    with db.begin() as c:
        pb = induct(*load_history())[1]
        pb = pb.model_copy(update={"status": "active"})
        pbstore.save(c, pb, "test", DAY)
        ref = {"id": pb.id, "version": pb.version, "branch": pb.branches[0].id}
        approvals.create(c, _proposal("PRP-501", amount="501", playbook=ref), DAY)
        approvals.create(c, _proposal("PRP-500", amount="500", playbook=ref), DAY)
        assert approvals.route(c, "PRP-501", DAY) == "controller"
        assert approvals.route(c, "PRP-500", DAY) == "approver"
    with pytest.raises(approvals.PolicyViolation):
        with db.begin() as c:
            approvals.decide(c, "PRP-501", approvals.APPROVER_AGENT, "approved", "try", DAY)
    with db.begin() as c:
        approvals.decide(c, "PRP-500", approvals.APPROVER_AGENT, "approved", "within policy", DAY)
        approvals.create(c, _proposal("PRP-NOPB", amount="10"), DAY)  # not from an active playbook
        assert approvals.route(c, "PRP-NOPB", DAY) == "controller"
        draft = pb.model_copy(update={"version": 2, "status": "draft"})
        pbstore.save(c, draft, "learner", DAY)

    class _RP:  # minimal replay stand-in for the workflow's playbook approval
        eng, matcher, sources = db, None, None
    wf = Workflow.__new__(Workflow)
    with pytest.raises(approvals.PolicyViolation):
        with db.begin() as c:
            wf.approve_playbook(c, pb.id, 2, approvals.APPROVER_AGENT, DAY, "agent tries")
    with db.connect() as c:
        assert pbstore.get(c, pb.id, 2).status == "draft"


EXPECTED_ROLE = {"B1": ("client_service", "notify"), "B2": ("client", "notify"), "B3": ("broker_desk", "request"),
                 "N2": ("client", "request")}


def test_T21_routing(quarter):
    """R18: each break type's outreach reaches the role named in its playbook."""
    eng, rp = quarter["eng"], quarter["replay"]
    with eng.connect() as c:
        brk = {r.detection_key: r.case_id for r in c.execute(text("SELECT detection_key, case_id FROM breaks"))}
        msgs = [dict(r) for r in c.execute(select(messages)).mappings()]
    for ak in rp.store.answer_key:
        if ak["type"] not in EXPECTED_ROLE:
            continue
        role, kind = EXPECTED_ROLE[ak["type"]]
        cid = brk[ak["expected_detection_keys"][0]]
        got = [(m["to_role"], m["kind"]) for m in msgs if m["case_id"] == cid]
        assert (role, kind) in got, f"{ak['break_id']} {cid}: {got}"
    names = {m["to_role"]: m["to_name"] for m in msgs}
    assert names["client_service"] == "Alex Kim" and names["broker_desk"] == "Northgate Execution"


def test_T22_follow_up_and_escalation(quarter):
    """R18: with no reply, a follow-up fires at business day 1 and escalation at business day 3."""
    from bp.calendar import bd_between

    with quarter["eng"].connect() as c:
        msgs = [dict(r) for r in c.execute(select(messages).order_by(messages.c.msg_id)).mappings()]
    unanswered = [m for m in msgs if m["kind"] == "request" and not m["answered"]]
    assert unanswered, "expected the non-paying client's cash call to stay unanswered"
    req = unanswered[0]
    fu = [m for m in msgs if m["reply_to"] == req["msg_id"] and m["kind"] == "follow_up"]
    esc = [m for m in msgs if m["reply_to"] == req["msg_id"] and m["kind"] == "escalation"]
    assert len(fu) == 1 and bd_between(req["sent_on"], fu[0]["sent_on"]) == 1
    assert len(esc) == 1 and bd_between(req["sent_on"], esc[0]["sent_on"]) == 3 and esc[0]["to_role"] == "controller"
    answered = [m for m in msgs if m["kind"] == "request" and m["answered"]]
    for m in answered:  # people who reply within SLA never get chased
        assert not [x for x in msgs if x["reply_to"] == m["msg_id"] and x["kind"] == "escalation"]
