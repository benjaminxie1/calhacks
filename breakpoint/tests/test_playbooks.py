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
    for ak in rp.store.answer_key:
        if ak["type"] in EXPECT:
            case = cs[brk[ak["expected_detection_keys"][0]]]
            assert (case["playbook_id"], case["branch_id"]) == EXPECT[ak["type"]], ak["break_id"]
    # every known case's facts hit exactly one branch of its playbook
    with quarter["eng"].connect() as c:
        for case in cs.values():
            if case["playbook_id"] and case["branch_id"]:
                pb = pbstore.get(c, case["playbook_id"], case["playbook_version"])
                for f in case["facts"].values():
                    assert match_branches(pb, from_jsonable(f)) == [case["branch_id"]]
    # the March gross-up hit a key with no playbook -> needs human before it was taught
    with quarter["eng"].connect() as c:
        mar = next(cs[brk[k]] for k in brk if k.startswith("gross_up:") and "LSE-012" in k)
        first = c.execute(select(events.c.reason).where(events.c.case_id == mar["case_id"],
                                                        events.c.action == "case_status")
                          .order_by(events.c.seq)).scalars().all()
    assert any("needs_human" in r and "no active playbook" in r for r in first)
    # overlap: two branches that both match -> escalate
    pb = Playbook.model_validate({**GOOD, "status": "active", "branches": GOOD["branches"] + [
        {**GOOD["branches"][0], "id": "b", "when": "custodian_qty > 0"}]})
    assert match_branches(pb, {"ledger_qty": 5, "custodian_qty": 5}) == ["a", "b"]


EXPECT = {"share": ("wf-statement-lease-tenant-share", "occupied-area-share"),
          "admin_fee": ("wf-statement-lease-admin-fee", "fee-over-limit"),
          "cap": ("wf-statement-lease-controllable-cap", "cap-not-applied"),
          "capital": ("wf-statement-lease-capital-exclusion", "capital-excluded"),
          "tax": ("wf-statement-county-property-tax", "tax-above-county"),
          "estimates": ("wf-statement-ap-estimates-credited", "estimates-missed")}


def test_T25_drafts_cannot_run(db, store):
    """R21: a draft playbook is never used; the case goes to needs human."""
    rp = Replay(eng=db, store=store, agents=False)
    rp.init(fresh=True)
    rp.run(date(2026, 2, 2), date(2026, 3, 3))  # the showcase statement arrives Mar 3
    cs, _ = load_history()
    pb = next(p for p in induct(cs, "") if p.id == "wf-statement-lease-tenant-share")
    with db.begin() as c:
        pbstore.save(c, pb, "learner", date(2026, 3, 3))  # status draft
        b = dict(c.execute(text("SELECT * FROM breaks WHERE detection_key LIKE 'share:%' LIMIT 1")).mappings().one())
        res = Investigator(rp.matcher).investigate(c, {"case_id": "CASE-T", "break_key": b["break_key"]}, [b],
                                                   date(2026, 3, 3))
    assert res["status"] == "needs_human" and "no active playbook" in res["reason"] and "draft" in res["reason"]


@pytest.mark.llm
@pytest.mark.parametrize("attempt", range(LLM_RUNS))
def test_T26_induction(attempt):
    """R20: 30 cases from last season plus the SOP produce one draft per known finding with the expected actions."""
    cs, sop = load_history()
    assert len(cs) == 30 and "Known findings" in sop
    pbs = {p.id: p for p in induct(cs, sop)}
    assert all(p.status == "draft" for p in pbs.values())
    want = {"wf-statement-lease-tenant-share": {"occupied-area-share": ["share_credit", "dispute_notice"]},
            "wf-statement-lease-admin-fee": {"fee-over-limit": ["fee_credit", "dispute_notice"]},
            "wf-statement-lease-controllable-cap": {"cap-not-applied": ["cap_credit", "dispute_notice"]},
            "wf-statement-lease-capital-exclusion": {"capital-excluded": ["capital_credit", "dispute_notice"]},
            "wf-statement-county-property-tax": {"tax-above-county": ["tax_credit", "request_tax_bills"]},
            "wf-statement-ap-estimates-credited": {"estimates-missed": ["estimates_credit", "send_payment_proof"]}}
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
    """R21: the first gross-up hits else, teaching adds a gross-up branch, the controller approves, then the May
    gross-ups (three stores, one landlord) complete automatically, citing the branch and teacher, with correct amounts."""
    eng, rp = quarter["eng"], quarter["replay"]
    with eng.connect() as c:
        brk = {r.detection_key: r.case_id for r in c.execute(text("SELECT detection_key, case_id FROM breaks"))}
        cs = {r.case_id: dict(r._mapping) for r in c.execute(select(cases))}
        pb_rows = [dict(r._mapping) for r in c.execute(select(playbooks).where(
            playbooks.c.id == "wf-statement-lease-gross-up"))]
        teach = c.execute(text("SELECT * FROM teach_sessions")).mappings().all()
    rows = sorted([r for r in rp.store.answer_key if r["type"] == "gross_up"], key=lambda r: r["injected_on"])
    mar = cs[brk[rows[0]["expected_detection_keys"][0]]]
    may = cs[brk[rows[1]["expected_detection_keys"][0]]]
    assert len(rows[1]["expected_detection_keys"]) == 3
    assert {brk[k] for k in rows[1]["expected_detection_keys"]} == {may["case_id"]}  # one case for all three stores
    assert mar["status"] == "completed" and mar["completion"]["taught_by"] == "Dana Ortiz"
    assert any(t["case_id"] == mar["case_id"] and t["fact"] == "gross_up_not_in_lease" for t in teach)
    assert pb_rows and pb_rows[0]["status"] == "active" and pb_rows[0]["approved_by"] == "Sam Lee"
    branch = pb_rows[0]["body"]["branches"][0]
    assert "gross_up_not_in_lease" in branch["when"] and branch["provenance"]["from_case"] == mar["case_id"]
    assert may["status"] == "completed" and may["playbook_id"] == "wf-statement-lease-gross-up"
    assert may["completion"]["branch"] == "gross-up-not-allowed"
    assert may["completion"]["provenance"]["taught_by"] == "Dana Ortiz"
    assert may["completion"]["provenance"]["from_case"] == mar["case_id"]
    assert may["opened_on"] == may["closed_on"]  # no human in the loop the second time
    from bp.eval import evaluate
    with eng.connect() as c:
        r = evaluate(c, rp.store)
    gross_rows = [x for x in r["resolution"]["rows"] if x["type"] == "gross_up"]
    assert all(x["amounts_ok"] for x in gross_rows), gross_rows


def test_T28_revision_flag(db, store, monkeypatch):
    """R22: a failed verify or a controller override flags the playbook."""
    rp = Replay(eng=db, store=store, controller_mode="scripted")
    rp.init(fresh=True)
    monkeypatch.setattr(rp.matcher, "still_broken", lambda conn, b, d: True)
    rp.run(date(2026, 2, 2), date(2026, 2, 13))  # the first share credit executes on Feb 13
    with db.connect() as c:
        flagged = c.execute(text("SELECT id FROM playbooks WHERE status = 'flagged'")).scalars().all()
        case = c.execute(text("SELECT * FROM cases WHERE playbook_id IS NOT NULL ORDER BY case_id LIMIT 1")).mappings().one()
    assert "wf-statement-lease-tenant-share" in flagged
    assert case["status"] == "needs_human" and "verify failed" in case["notes"][-1]["text"]
    # override path
    with db.begin() as c:
        c.execute(text("UPDATE playbooks SET status = 'active' WHERE id = 'wf-statement-lease-tenant-share'"))
        rp.workflow.override(c, case["case_id"], "Sam Lee", "landlord re-measured the center; the lease share changed",
                             date(2026, 2, 17))
        st = c.execute(text("SELECT status FROM playbooks WHERE id = 'wf-statement-lease-tenant-share'")).scalar()
    assert st == "flagged"
