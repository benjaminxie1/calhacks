"""Learning: T23-T28."""
from datetime import date

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from bp.agents.core import Investigator, match_branches
from bp.agents.learner import induct, load_history
from bp.db import cases, events, playbooks
from bp.engine.replay import Replay
from bp.playbooks import store as pbstore
from bp.playbooks.schema import Playbook
from bp.recon.context import from_jsonable
from conftest import LLM_RUNS

GOOD = {
    "id": "wf-x", "version": 1, "status": "draft", "key": {"sources": ["ledger", "custodian"], "field": "qty"},
    "context": ["ledger_qty", "custodian_qty"],
    "branches": [{"id": "a", "when": "ledger_qty == custodian_qty", "trust": "custodian", "actions": ["hold"],
                  "provenance": {"taught_by": "Dana Ortiz", "from_case": "CASE-1"}}],
}


def test_T23_playbook_schema():
    """R19: an invalid playbook is rejected with field errors."""
    Playbook.model_validate(GOOD)
    bad_cases = [
        ({**GOOD, "status": "live"}, "status"),
        ({**GOOD, "branches": [{**GOOD["branches"][0], "actions": ["wire_money_somewhere"]}]}, "unknown actions"),
        ({**GOOD, "branches": [{**GOOD["branches"][0], "when": "executions_qty == 1"}]}, "not in context"),
        ({**GOOD, "branches": [{**GOOD["branches"][0], "when": "__import__('os')"}]}, "not allowed"),
        ({**GOOD, "branches": [{**GOOD["branches"][0], "when": "ledger_qty == 1.5"}]}, "float"),
        ({k: v for k, v in GOOD.items() if k != "context"}, "context"),
        ({**GOOD, "version": 0}, "version"),
    ]
    for body, msg in bad_cases:
        with pytest.raises(ValidationError, match=msg):
            Playbook.model_validate(body)


def test_T24_playbook_match(quarter):
    """R19, R21: each break key routes to its playbook; each known case hits exactly one branch; novel cases hit
    else; overlapping branches escalate."""
    rp = quarter["replay"]
    with quarter["eng"].connect() as c:
        brk = {r.detection_key: r.case_id for r in c.execute(text("SELECT detection_key, case_id FROM breaks"))}
        cs = {r.case_id: dict(r._mapping) for r in c.execute(select(cases))}
    expect = {"B1": ("wf-ledger-custodian-trade-qty", "partial-fill"), "B3": ("wf-ledger-custodian-trade-qty", "duplicate-report"),
              "B2": ("wf-ledger-fee-schedule-commission", "stale-schedule"), "B4": ("wf-ledger-custodian-cash", "dividend-missing")}
    for ak in rp.store.answer_key:
        if ak["type"] in expect:
            case = cs[brk[ak["expected_detection_keys"][0]]]
            assert (case["playbook_id"], case["branch_id"]) == expect[ak["type"]], ak["break_id"]
    # every known case's facts hit exactly one branch of its playbook
    with quarter["eng"].connect() as c:
        for case in cs.values():
            if case["playbook_id"] and case["branch_id"]:
                pb = pbstore.get(c, case["playbook_id"], case["playbook_version"])
                for f in case["facts"].values():
                    assert match_branches(pb, from_jsonable(f)) == [case["branch_id"]]
    # the August split hit a key with no playbook -> needs human before it was taught
    with quarter["eng"].connect() as c:
        aug = next(cs[brk[k]] for k in brk if k.startswith("position:") and "2026-08-13" in k)
        first = c.execute(select(events.c.reason).where(events.c.case_id == aug["case_id"],
                                                        events.c.action == "case_status")
                          .order_by(events.c.seq)).scalars().all()
    assert any("needs_human" in r and "no active playbook" in r for r in first)
    # overlap: two branches that both match -> escalate
    pb = Playbook.model_validate({**GOOD, "status": "active", "branches": GOOD["branches"] + [
        {**GOOD["branches"][0], "id": "b", "when": "custodian_qty > 0"}]})
    assert match_branches(pb, {"ledger_qty": 5, "custodian_qty": 5}) == ["a", "b"]


def test_T25_drafts_cannot_run(db, store):
    """R21: a draft playbook is never used; the case goes to needs human."""
    rp = Replay(eng=db, store=store, agents=False)
    rp.init(fresh=True)
    rp.run(date(2026, 7, 1), date(2026, 7, 8))  # first planted B1 settles Jul 8
    cs, _ = load_history()
    pb = next(p for p in induct(cs, "") if p.id == "wf-ledger-custodian-trade-qty")
    with db.begin() as c:
        pbstore.save(c, pb, "learner", date(2026, 7, 8))  # status draft
        b = dict(c.execute(text("SELECT * FROM breaks WHERE detection_key LIKE 'trade_qty:%' LIMIT 1")).mappings().one())
        res = Investigator(rp.matcher).investigate(c, {"case_id": "CASE-T", "break_key": b["break_key"]}, [b],
                                                   date(2026, 7, 8))
    assert res["status"] == "needs_human" and "no active playbook" in res["reason"] and "draft" in res["reason"]


@pytest.mark.llm
@pytest.mark.parametrize("attempt", range(LLM_RUNS))
def test_T26_induction(attempt):
    """R20: 30 historical cases plus the SOP produce drafts for B1-B4 with the expected actions."""
    cs, sop = load_history()
    assert len(cs) == 30 and "Known breaks" in sop
    pbs = {p.id: p for p in induct(cs, sop)}
    assert all(p.status == "draft" for p in pbs.values())
    want = {"wf-ledger-custodian-trade-qty": {"partial-fill": ["reverse_unfilled", "notify_client_service"],
                                               "duplicate-report": ["request_info_broker", "reverse_duplicate"]},
            "wf-ledger-fee-schedule-commission": {"stale-schedule": ["fee_refund", "notify_client"]},
            "wf-ledger-custodian-cash": {"dividend-missing": ["post_dividend"]}}
    assert set(pbs) == set(want)
    for pid, branches in want.items():
        assert {b.id: b.actions for b in pbs[pid].branches} == branches
    # every historical case hits exactly its own branch
    for c in cs:
        pb = next(p for p in pbs.values() if p.key.name() == c["break_key"])
        hits = match_branches(pb, from_jsonable(c["facts"]))
        assert len(hits) == 1
        assert next(b for b in pb.branches if b.id == hits[0]).actions == c["resolution"]["actions"]


@pytest.mark.llm
@pytest.mark.parametrize("attempt", range(LLM_RUNS))
def test_T27_learning_loop_end_to_end(quarter, attempt):
    """R21: first split hits else, teaching adds a split branch, the controller approves, then the second split
    completes automatically, citing the branch and teacher, with correct amounts."""
    eng, rp = quarter["eng"], quarter["replay"]
    with eng.connect() as c:
        brk = {r.detection_key: r.case_id for r in c.execute(text("SELECT detection_key, case_id FROM breaks"))}
        cs = {r.case_id: dict(r._mapping) for r in c.execute(select(cases))}
        pb_rows = [dict(r._mapping) for r in c.execute(select(playbooks).where(
            playbooks.c.id == "wf-ledger-custodian-position-qty"))]
        teach = c.execute(text("SELECT * FROM teach_sessions")).mappings().all()
    n1 = sorted([r for r in rp.store.answer_key if r["type"] == "N1"], key=lambda r: r["injected_on"])
    aug = cs[brk[n1[0]["expected_detection_keys"][0]]]
    octo = cs[brk[n1[1]["expected_detection_keys"][0]]]
    assert {brk[k] for k in n1[1]["expected_detection_keys"]} == {octo["case_id"]}  # one case for all holders
    assert aug["status"] == "completed" and aug["completion"]["taught_by"] == "Dana Ortiz"
    assert any(t["case_id"] == aug["case_id"] and t["fact"] == "split_on_symbol" for t in teach)
    assert pb_rows and pb_rows[0]["status"] == "active" and pb_rows[0]["approved_by"] == "Sam Lee"
    branch = pb_rows[0]["body"]["branches"][0]
    assert "split_on_symbol" in branch["when"] and branch["provenance"]["from_case"] == aug["case_id"]
    assert octo["status"] == "completed" and octo["playbook_id"] == "wf-ledger-custodian-position-qty"
    assert octo["completion"]["branch"] == "split"
    assert octo["completion"]["provenance"]["taught_by"] == "Dana Ortiz"
    assert octo["completion"]["provenance"]["from_case"] == aug["case_id"]
    assert octo["opened_on"] == octo["closed_on"]  # no human in the loop the second time
    from bp.eval import evaluate
    with eng.connect() as c:
        r = evaluate(c, rp.store)
    n1_rows = [x for x in r["resolution"]["rows"] if x["type"] == "N1"]
    assert all(x["amounts_ok"] for x in n1_rows), n1_rows


def test_T28_revision_flag(db, store, monkeypatch):
    """R22: a failed verify or a controller override flags the playbook."""
    rp = Replay(eng=db, store=store, controller_mode="scripted")
    rp.init(fresh=True)
    monkeypatch.setattr(rp.matcher, "still_broken", lambda conn, b, d: True)
    rp.run(date(2026, 7, 1), date(2026, 7, 10))  # the first controller-approved fix executes on Jul 9
    with db.connect() as c:
        flagged = c.execute(text("SELECT id FROM playbooks WHERE status = 'flagged'")).scalars().all()
        case = c.execute(text("SELECT * FROM cases WHERE playbook_id IS NOT NULL ORDER BY case_id LIMIT 1")).mappings().one()
    assert "wf-ledger-custodian-trade-qty" in flagged
    assert case["status"] == "needs_human" and "verify failed" in case["notes"][-1]["text"]
    # override path
    with db.begin() as c:
        c.execute(text("UPDATE playbooks SET status = 'active' WHERE id = 'wf-ledger-custodian-trade-qty'"))
        rp.workflow.override(c, case["case_id"], "Sam Lee", "booked to the wrong account, not a partial fill",
                             date(2026, 7, 13))
        st = c.execute(text("SELECT status FROM playbooks WHERE id = 'wf-ledger-custodian-trade-qty'")).scalar()
    assert st == "flagged"
