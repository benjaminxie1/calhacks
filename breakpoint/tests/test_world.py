"""World and data: T1, T2, T3, T5, plus the source-service key checks (step 4)."""
import typing
from collections import defaultdict
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Float

from bp import calendar, db, models
from bp.money import D
from bp.sources.api import KEYS, KeyRejected, SourceClient, make_app
from bp.world.generate import build


def test_T1_determinism(tmp_path):
    """R1: two runs with seed 42 produce identical file hashes."""
    _, h1 = build(42, out=tmp_path / "a")
    _, h2 = build(42, out=tmp_path / "b")
    assert h1 == h2
    assert (tmp_path / "a" / "booking.jsonl").read_bytes() == (tmp_path / "b" / "booking.jsonl").read_bytes()
    _, h3 = build(43, out=tmp_path / "c")
    assert h3["orders.jsonl"] != h1["orders.jsonl"]


def test_T2_true_world_consistency(store):
    """R3: positions rebuilt from true trades plus corporate actions equal the custodian's true positions every day."""
    pos: dict = defaultdict(Decimal)
    by_settle = defaultdict(list)
    for t in store.truth:
        by_settle[t.settle_date].append(t)
    for d in calendar.business_days():
        for t in by_settle.get(d, []):
            for p in t.postings:
                if p.account.startswith("client:") and p.asset != "USD" and p.account.count(":") == 1:
                    pos[(p.account.split(":")[1], p.asset)] += p.amount
        cust = store.custodian_positions(d)
        mine = {k: v for k, v in pos.items() if v != 0}
        assert mine == cust, f"positions diverge on {d}"
    assert len(calendar.business_days()) == 81
    assert len(store.accounts) == 200 and len(store.symbols) == 40
    assert 20000 <= len(store.orders) <= 26000


def test_T3_answer_key_completeness(store):
    """R4: every injected break has a key row with records and the expected fix."""
    ak = store.answer_key
    counts = defaultdict(int)
    for r in ak:
        row = models.AnswerKeyRow(**r)
        counts[row.type] += 1
        assert row.affected and row.expected_detection_keys and row.root_cause_source
        if row.type != "H2":
            assert row.expected_fix, row.break_id
    assert dict(counts) == {"B1": 6, "B2": 6, "B3": 4, "B4": 3, "H2": 10, "N1": 2, "N2": 2}


def _float_annotations(model) -> list[str]:
    bad = []
    for name, f in model.model_fields.items():
        if "float" in repr(f.annotation):
            bad.append(f"{model.__name__}.{name}")
    return bad


def test_T5_no_floats(world_dir):
    """R6: no float types in models, database columns or the world files."""
    bad = []
    for m in vars(models).values():
        if isinstance(m, type) and issubclass(m, models.Model) and m is not models.Model:
            bad += _float_annotations(m)
    bad += [f"{t.name}.{c.name}" for t in db.md.sorted_tables for c in t.columns if isinstance(c.type, Float)]
    assert not bad, bad
    import json
    for name in ["booking.jsonl", "custodian_settlements.jsonl", "prices.jsonl"]:
        with (world_dir / name).open() as f:
            for i, line in enumerate(f):
                if i > 2000:
                    break
                json.loads(line, parse_float=lambda x: pytest.fail(f"float literal {x} in {name}"))
    with pytest.raises(TypeError):
        D(1.5)


def test_sources_serve_by_date_and_reject_other_keys(store):
    """R2 / step 4: each service returns a given day's data and rejects other services' keys."""
    d = date(2026, 7, 8)
    for src, ep in [("oms", "orders"), ("executions", "executions"), ("custodian", "settlements"), ("bank", "transfers"),
                    ("reference", "prices"), ("fees", "schedule"), ("directory", "people"), ("booking", "txns")]:
        assert SourceClient(src, KEYS[src], store).get(ep, d)
        other = KEYS["oms" if src != "oms" else "bank"]
        with pytest.raises(KeyRejected):
            SourceClient(src, other, store).get(ep, d)
    http = TestClient(make_app(store))
    assert http.get("/oms/orders", params={"d": "2026-07-08"}, headers={"x-api-key": KEYS["oms"]}).status_code == 200
    assert http.get("/oms/orders", params={"d": "2026-07-08"}, headers={"x-api-key": KEYS["bank"]}).status_code == 401
    orders = http.get("/oms/orders", params={"d": "2026-07-08"}, headers={"x-api-key": KEYS["oms"]}).json()
    assert orders and all(o["trade_date"] == "2026-07-08" for o in orders)
