"""World and data: T1, T2, T3, T5, plus the source-service key checks (step 4)."""
import typing
from collections import defaultdict
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Float

from bp import calendar, cam, db, models
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
    assert h3["centers.jsonl"] != h1["centers.jsonl"]


def test_T2_true_world_consistency(store):
    """R3: every true statement is exactly what its lease calls for, and the true books carry it."""
    assert len(calendar.business_days()) == 83
    assert len(store.leases) == 40 and len(store.landlords) == 12 and len(store.statements) == 40
    true_books: dict = defaultdict(Decimal)
    for t in store.truth:
        for p in t.postings:
            true_books[p.account] += p.amount
    for st in store.truth_statements:
        lease = store.lease(st.lease_id)
        tax = store.tax_record(store.center(lease.center_id).apn)
        found = cam.findings(st, lease, tax, store.cleared_estimates(lease.lease_id, 2025))
        assert {k for k in found if k != "rounding"} == set(), (st.lease_id, found)
        assert st.tenant_total == sum((l.tenant_amount for l in st.lines), D(0))
        sent = cam.findings(store.statement(st.statement_id), lease, tax, store.cleared_estimates(lease.lease_id, 2025))
        rounding = sent["rounding"]["amount"] if sent.get("rounding", {}).get("harmless") else D(0)  # accepted per SOP
        assert true_books[f"lease:{lease.lease_id}:expense"] == st.tenant_total + rounding
        # every clause the abstract quotes appears word for word in the lease text
        text = store.lease_text(lease.lease_id)
        assert all(c.quote in text for c in lease.clauses.values()), lease.lease_id
    showcase = next(x for x in store.truth_statements if x.lease_id == "LSE-007")
    assert showcase.tenant_total == D("24127.50")  # the CAM core sample's answer


def test_T3_answer_key_completeness(store):
    """R4: every planted error has a key row with records and the expected fix."""
    ak = store.answer_key
    counts = defaultdict(int)
    for r in ak:
        row = models.AnswerKeyRow(**r)
        counts[row.type] += 1
        assert row.affected and row.expected_detection_keys and row.root_cause_source
        if row.type != "rounding":
            assert row.expected_fix, row.break_id
    assert dict(counts) == {"share": 4, "admin_fee": 5, "cap": 4, "capital": 4, "tax": 5, "estimates": 3,
                            "gross_up": 2, "rounding": 6}


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
    for name in ["booking.jsonl", "statements.jsonl", "leases.jsonl", "payments.jsonl"]:
        with (world_dir / name).open() as f:
            for i, line in enumerate(f):
                if i > 2000:
                    break
                json.loads(line, parse_float=lambda x: pytest.fail(f"float literal {x} in {name}"))
    with pytest.raises(TypeError):
        D(1.5)


def test_sources_serve_by_date_and_reject_other_keys(store):
    """R2 / step 4: each service returns a given day's data and rejects other services' keys."""
    d = date(2026, 3, 3)  # the showcase statement arrives
    for src, ep in [("leases", "abstracts"), ("statements", "received"), ("ap", "payments"), ("bank", "items"),
                    ("county", "tax_records"), ("reference", "centers"), ("directory", "people"), ("booking", "txns")]:
        assert SourceClient(src, KEYS[src], store).get(ep, d)
        other = KEYS["leases" if src != "leases" else "bank"]
        with pytest.raises(KeyRejected):
            SourceClient(src, other, store).get(ep, d)
    http = TestClient(make_app(store))
    q = {"d": "2026-03-03"}
    assert http.get("/statements/received", params=q, headers={"x-api-key": KEYS["statements"]}).status_code == 200
    assert http.get("/statements/received", params=q, headers={"x-api-key": KEYS["bank"]}).status_code == 401
    got = http.get("/statements/received", params=q, headers={"x-api-key": KEYS["statements"]}).json()
    assert got and all(x["received_on"] == "2026-03-03" for x in got)
