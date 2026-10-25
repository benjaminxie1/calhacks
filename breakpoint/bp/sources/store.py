"""In-memory views of each source system, served by simulated date.

Loads a generated world directory and answers "what does system X say as of day d".
Live injections are applied as overlays. Nothing here is the ledger: the ledger is
Postgres, and the booking feed here is what AP sends to it.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from bp import config
from bp.models import BankItem, Center, EstimatePayment, Landlord, Lease, LedgerTxn, Person, Statement, TaxRecord
from bp.money import D


def _load(path: Path, model):
    if not path.exists():
        return []
    out = []
    with path.open() as f:
        for line in f:
            obj = json.loads(line)
            out.append(model(**obj) if model else obj)
    return out


class SourceStore:
    def __init__(self, world_dir: Path):
        self.dir = world_dir
        self.landlords: list[Landlord] = _load(world_dir / "landlords.jsonl", Landlord)
        self.centers: list[Center] = _load(world_dir / "centers.jsonl", Center)
        self.leases: list[Lease] = _load(world_dir / "leases.jsonl", Lease)
        self.people: list[Person] = _load(world_dir / "people.jsonl", Person)
        self.tax_records: list[TaxRecord] = _load(world_dir / "tax_records.jsonl", TaxRecord)
        self.payments: list[EstimatePayment] = _load(world_dir / "payments.jsonl", EstimatePayment)
        self.bank: list[BankItem] = _load(world_dir / "bank.jsonl", BankItem)
        self.statements: list[Statement] = _load(world_dir / "statements.jsonl", Statement)
        self.truth_statements: list[Statement] = _load(world_dir / "truth_statements.jsonl", Statement)
        self.booking: list[LedgerTxn] = _load(world_dir / "booking.jsonl", LedgerTxn)
        self.truth: list[LedgerTxn] = _load(world_dir / "truth.jsonl", LedgerTxn)
        self.answer_key: list[dict] = _load(world_dir / "answer_key.jsonl", None)
        self.injected: list[str] = []
        self._reindex()

    # ------------------------------------------------------------- indexes
    def _reindex(self) -> None:
        self.lease_by_id = {x.lease_id: x for x in self.leases}
        self.center_by_id = {x.center_id: x for x in self.centers}
        self.landlord_by_id = {x.landlord_id: x for x in self.landlords}
        self.tax_by_apn = {x.apn: x for x in self.tax_records}
        self.stmt_by_id = {x.statement_id: x for x in self.statements}
        self.stmts_by_day = defaultdict(list)
        for x in self.statements:
            self.stmts_by_day[x.received_on].append(x)
        self.payments_by_lease = defaultdict(list)
        for p in self.payments:
            self.payments_by_lease[p.lease_id].append(p)
        self.bank_by_id = {b.payment_id: b for b in self.bank}
        self.booking_by_day = defaultdict(list)
        for t in self.booking:
            self.booking_by_day[t.date].append(t)

    # ------------------------------------------------------------- 1. reference: centers and landlords
    def center(self, center_id: str) -> Center:
        return self.center_by_id[center_id]

    def landlord(self, landlord_id: str) -> Landlord:
        return self.landlord_by_id[landlord_id]

    # ------------------------------------------------------------- 2. lease administration
    def lease(self, lease_id: str) -> Lease:
        return self.lease_by_id[lease_id]

    def lease_text(self, lease_id: str) -> str:
        p = self.dir / "leases" / f"{lease_id}.md"
        return p.read_text() if p.exists() else ""

    # ------------------------------------------------------------- 3. landlord statements
    def statements_received(self, on: date) -> list[Statement]:
        return self.stmts_by_day.get(on, [])

    def statement(self, statement_id: str) -> Statement:
        return self.stmt_by_id[statement_id]

    def statements_asof(self, asof: date) -> list[Statement]:
        return [x for x in self.statements if x.received_on <= asof]

    # ------------------------------------------------------------- 4. accounts payable
    def estimate_payments(self, lease_id: str, year: int) -> list[EstimatePayment]:
        return [p for p in self.payments_by_lease.get(lease_id, []) if p.period.startswith(str(year))]

    # ------------------------------------------------------------- 5. bank
    def bank_status(self, payment_id: str) -> BankItem | None:
        return self.bank_by_id.get(payment_id)

    def cleared_estimates(self, lease_id: str, year: int) -> Decimal:
        return sum((p.amount for p in self.estimate_payments(lease_id, year)
                    if (b := self.bank_by_id.get(p.payment_id)) and b.status == "cleared"), D(0))

    # ------------------------------------------------------------- 6. county tax records
    def tax_record(self, apn: str) -> TaxRecord | None:
        return self.tax_by_apn.get(apn)

    # ------------------------------------------------------------- 7. booking feed
    def booking_feed(self, on: date) -> list[LedgerTxn]:
        return self.booking_by_day.get(on, [])

    # ------------------------------------------------------------- 8. directory
    def person(self, role: str) -> Person:
        return next(p for p in self.people if p.role == role)

    # ------------------------------------------------------------- live injection overlay
    def apply_injection(self, inj_id: str, records: dict) -> None:
        if inj_id in self.injected:
            return
        self.injected.append(inj_id)
        for x in records.get("statements", []):
            st = Statement(**x)
            self.statements = [s for s in self.statements if s.statement_id != st.statement_id] + [st]
        for key, rows in (("booking", records.get("booking", [])), ("truth", records.get("truth", []))):
            for x in rows:
                t = LedgerTxn(**x)
                setattr(self, key, [y for y in getattr(self, key) if y.source_ref != t.source_ref] + [t])
        self.answer_key += records.get("answer_key", [])
        self._reindex()


@lru_cache(maxsize=4)
def _cached(path: str) -> SourceStore:
    return SourceStore(Path(path))


def get_store(seed: int = config.SEED, clean: bool = False) -> SourceStore:
    d = config.world_dir(seed, clean)
    if not (d / "manifest.json").exists():
        from bp.world.generate import build

        build(seed, clean)
    return _cached(str(d))
