"""Detection: T4, T12-T15."""
import pytest
from sqlalchemy import select, text

from bp.db import breaks
from bp.engine.replay import Replay
from bp.sources.store import get_store

KEY_PREFIX = {"share": "share:", "admin_fee": "admin_fee:", "cap": "cap:", "capital": "capital:", "tax": "tax:",
              "estimates": "estimates:", "gross_up": "gross_up:", "rounding": "rounding:"}


def _breaks(eng):
    with eng.connect() as c:
        return [dict(r) for r in c.execute(select(breaks)).mappings()]


def test_T4_clean_world(db):
    """R5, R12: recon on the clean world (every statement exactly what its lease calls for) finds no breaks."""
    rp = Replay(eng=db, store=get_store(clean=True), clean=True, agents=False)
    rp.init(fresh=True)
    rp.run()
    assert _breaks(db) == []
    with db.connect() as c:
        assert c.execute(text("SELECT count(*) FROM ledger_entries WHERE kind = 'statement'")).scalar() == 40


@pytest.mark.parametrize("kind", ["share", "admin_fee", "cap", "capital", "tax", "estimates", "gross_up"])
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
            assert found[k]["detected_on"].isoformat() == r["injected_on"]


def test_T13_auto_clear(quarter):
    """R13: a printed total within $1 of the line-by-line recomputation auto-clears with a reason and never escalates."""
    bs = _breaks(quarter["eng"])
    rounding = [b for b in bs if b["detection_key"].startswith("rounding:")]
    assert len(rounding) == 6
    assert all(b["status"] == "auto_cleared" and b["harmless"] and "tolerance" in b["reason"] and b["case_id"] is None
               for b in rounding)


def test_T14_full_detection_accuracy(quarter):
    """R12, R13: 100% of planted errors found, 0 unplanted breaks."""
    from bp.eval import evaluate

    with quarter["eng"].connect() as c:
        r = evaluate(c, quarter["replay"].store)
    assert r["detection"]["recall"] == "100%"
    assert r["detection"]["false_positives"] == []
    assert r["detection"]["precision"] == "100%"


def test_T15_rerun_idempotent(quarter):
    """R14: running recon again on the same data creates no duplicate breaks."""
    from datetime import date

    rp = quarter["replay"]
    before = len(_breaks(quarter["eng"]))
    for d in [r["date"] for r in quarter["days"][::5]]:
        with quarter["eng"].begin() as c:
            rp.matcher.run_day(c, date.fromisoformat(d))
    assert len(_breaks(quarter["eng"])) == before
    with quarter["eng"].connect() as c:
        assert c.execute(text("SELECT count(*) - count(DISTINCT detection_key) FROM breaks")).scalar() == 0
