"""Time, audit, proof, injection, demo: T29-T36."""
from datetime import date

import pytest
from sqlalchemy import select, text

from bp.agents.auditor import Auditor
from bp.db import breaks, cases, events, ledger_entries, proposals
from bp.engine.replay import Replay
from bp.eval import evaluate
from conftest import LLM_RUNS


def test_T29_resolution_correctness(quarter):
    """R11, R27: after replay the ledger equals the true world; proposal amounts equal the answer key."""
    with quarter["eng"].connect() as c:
        r = evaluate(c, quarter["replay"].store)
    assert r["ledger_vs_truth"]["equal"], r["ledger_vs_truth"]
    bad = [x for x in r["resolution"]["rows"] if not (x["status_ok"] and x["amounts_ok"])]
    assert not bad, bad
    assert r["invariants"]["all_green"], r["invariants"]


def test_T30_replay(quarter):
    """R23: 81 days finish without errors; period close, aging and escalation fire as specified."""
    assert len(quarter["days"]) == 81
    with quarter["eng"].connect() as c:
        errs = c.execute(select(events).where(events.c.action == "error")).all()
        closes = c.execute(select(events.c.sim_date, events.c.reason).where(events.c.action == "period_closed")
                           .order_by(events.c.seq)).all()
        late = c.execute(text("""SELECT count(*) FROM ledger_entries e JOIN periods p ON p.period = e.period
                                 WHERE e.kind = 'correction' AND p.closed_on IS NOT NULL AND e.effective_date >= p.closed_on""")).scalar()
    assert errs == []
    assert [(d.isoformat(), r.split()[0]) for d, r in closes] == [
        ("2026-08-05", "2026-07"), ("2026-09-03", "2026-08"), ("2026-10-05", "2026-09")]
    assert late == 0


def test_T31_audit_completeness(quarter):
    """R24: every state change has an event with actor, time, evidence and reason."""
    with quarter["eng"].connect() as c:
        evs = [dict(r) for r in c.execute(select(events)).mappings()]
        by_case = {}
        for e in evs:
            by_case.setdefault(e["case_id"], []).append(e)
        for cs in c.execute(select(cases)).mappings():
            acts = {e["action"] for e in by_case.get(cs["case_id"], [])}
            assert "case_opened" in acts and "case_status" in acts, cs["case_id"]
        for p in c.execute(select(proposals)).mappings():
            pe = [e for e in evs if e["inputs"].get("proposal_id") == p["proposal_id"]]
            assert {"proposal_created", "proposal_routed", "proposal_approved"} <= {e["action"] for e in pe}
            if p["status"] == "executed":
                assert any(e["action"] == "proposal_executed" for e in pe)
        fixes = c.execute(select(ledger_entries.c.proposal_id).where(ledger_entries.c.kind == "correction")).scalars().all()
        executed = {e["inputs"]["proposal_id"] for e in evs if e["action"] == "proposal_executed"}
        assert set(fixes) <= executed
        keys = {e["inputs"].get("detection_key") for e in evs if e["action"] in ("break_detected", "break_auto_cleared")}
        missing = [b.detection_key for b in c.execute(select(breaks.c.detection_key))
                   if b.detection_key not in keys and not b.detection_key.startswith("timing:")]
        assert missing == []
    assert all(e["actor"] and e["ts"] and e["reason"] for e in evs)
    assert any(e["evidence"] for e in evs if e["action"] == "proposal_created")


@pytest.mark.llm
@pytest.mark.parametrize("attempt", range(LLM_RUNS))
def test_T32_ask_why(quarter, attempt):
    """R24: a why-question about a completed case is answered citing its events and playbook."""
    with quarter["eng"].connect() as c:
        cid = c.execute(text("SELECT case_id FROM cases WHERE completion->>'branch' = 'split' LIMIT 1")).scalar()
        ans = Auditor().ask(c, "Why was this case resolved automatically, and who taught the fix?", cid)
        seqs = set(c.execute(select(events.c.seq)).scalars())
    assert ans["citations"] and set(ans["citations"]) <= seqs
    assert "wf-ledger-custodian-position-qty" in ans["answer"] and "Dana Ortiz" in ans["answer"]


def test_T33_live_injection(db, store):
    """R26: a B2 injected from the UI is detected and handled within one replay day."""
    rp = Replay(eng=db, store=store, controller_mode="scripted", teach_mode="scripted")
    rp.init(fresh=True)
    rp.run(date(2026, 7, 1), date(2026, 8, 20))
    acct = next(a.account_id for a in store.accounts if a.tier == "standard")
    inj = rp.inject("B2", acct, "KO")
    assert inj["effective_on"] == "2026-08-21"
    rp.run(date(2026, 8, 21), date(2026, 8, 21))
    with db.connect() as c:
        b = c.execute(select(breaks).where(breaks.c.detection_key == f"fee:{inj['order_id']}")).mappings().one()
        case = c.execute(select(cases).where(cases.c.case_id == b["case_id"])).mappings().one()
    assert b["detected_on"] == date(2026, 8, 21)
    assert case["status"] == "completed" and case["closed_on"] == date(2026, 8, 21)
    assert case["branch_id"] == "stale-schedule"


def test_T34_proof_page(quarter):
    """R27: the metrics the Proof page shows equal the eval script's output."""
    from fastapi.testclient import TestClient

    from bp.api.app import create_app

    app = create_app(eng=quarter["eng"], replay=quarter["replay"])
    shown = TestClient(app).get("/api/proof").json()
    with quarter["eng"].connect() as c:
        r = evaluate(c, quarter["replay"].store)
    from bp.agents.events import jsonable
    assert shown["detection"] == jsonable(r["detection"])
    assert shown["resolution"]["correct"] == r["resolution"]["correct"]
    assert shown["invariants"]["checks"] == r["invariants"]["checks"]


@pytest.mark.skip(reason="T35 generality (BenchRec connector) is the stretch step; first scope cut in the plan")
def test_T35_generality():
    pass


def test_T36_demo_script(world_dir):
    """make demo ends with every case in its expected state."""
    from bp import demo
    from conftest import _make_db

    r = demo.run(_make_db("breakpoint_demo_test"), quiet=True)
    assert demo.ok(r), r["resolution"]
