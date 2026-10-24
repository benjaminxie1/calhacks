"""Detection: T4, T12-T15."""
import pytest
from sqlalchemy import select, text

from bp.db import breaks
from bp.engine.replay import Replay
from bp.sources.store import get_store

KEY_PREFIX = {"B1": "trade_qty:", "B2": "fee:", "B3": "trade_qty:", "B4": "cash:", "N1": "position:", "N2": "bank:",
              "H2": "price:"}


def _breaks(eng):
    with eng.connect() as c:
        return [dict(r) for r in c.execute(select(breaks)).mappings()]


def test_T4_clean_world(db):
    """R5, R12: recon on the clean world (zero injected breaks) finds no breaks; only T+1 timing items, all cleared."""
    rp = Replay(eng=db, store=get_store(clean=True), clean=True, agents=False)
    rp.init(fresh=True)
    rp.run()
    bs = _breaks(db)
    assert [b["detection_key"] for b in bs if b["break_key"] != "ledger-custodian:settlement_timing"] == []
    assert all(b["harmless"] for b in bs)
    assert sum(b["status"] == "open" for b in bs) <= 1  # the last day's trades settle after the window


@pytest.mark.parametrize("kind", ["B1", "B2", "B3", "B4", "N1", "N2"])
def test_T12_detect_each_type(quarter, kind):
    """R12: each planted type is detected with the right break key and detection keys."""
    rp = quarter["replay"]
    found = {b["detection_key"]: b for b in _breaks(quarter["eng"])}
    rows = [r for r in rp.store.answer_key if r["type"] == kind]
    assert rows
    for r in rows:
        for k in r["expected_detection_keys"]:
            assert k in found, f"{r['break_id']} not detected: {k}"
            assert k.startswith(KEY_PREFIX[kind])


def test_T13_auto_clear(quarter):
    """R13: T+1 timing and <= $0.01 rounding auto-clear with a reason and never escalate."""
    bs = _breaks(quarter["eng"])
    h2 = [b for b in bs if b["detection_key"].startswith("price:")]
    timing = sorted((b for b in bs if b["detection_key"].startswith("timing:")), key=lambda b: b["detected_on"])
    assert len(h2) == 10 and all(b["status"] == "auto_cleared" and b["reason"] and b["case_id"] is None for b in h2)
    assert len(timing) == 81
    assert all(b["status"] == "auto_cleared" and "settled" in b["reason"] for b in timing[:-1])
    assert all(b["case_id"] is None for b in timing)


def test_T14_full_detection_accuracy(quarter):
    """R12, R13: 100% of planted breaks found, 0 unplanted breaks."""
    from bp.eval import evaluate

    with quarter["eng"].connect() as c:
        r = evaluate(c, quarter["replay"].store)
    assert r["detection"]["recall"] == "100%"
    assert r["detection"]["false_positives"] == []
    assert r["detection"]["precision"] == "100%"


def test_T15_rerun_idempotent(quarter):
    """R14: running recon again on the same data creates no duplicate breaks."""
    rp = quarter["replay"]
    before = len(_breaks(quarter["eng"]))
    for d in [r["date"] for r in quarter["days"][:: 10]]:
        from datetime import date
        with quarter["eng"].begin() as c:
            rp.matcher.run_transaction(c, date.fromisoformat(d))
    with quarter["eng"].begin() as c:
        rp.matcher.recon_eod(c, date.fromisoformat(quarter["days"][-1]["date"]))
    assert len(_breaks(quarter["eng"])) == before
    with quarter["eng"].connect() as c:
        assert c.execute(text("SELECT count(*) - count(DISTINCT detection_key) FROM breaks")).scalar() == 0
