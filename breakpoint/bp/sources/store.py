"""In-memory views of each source system, served by simulated date.

Loads a generated world directory and answers "what does system X say as of day d".
Live injections are applied as overlays. Nothing here is the ledger: the ledger is
Postgres, and the booking feed here is what the booking system sends to it.
"""
from __future__ import annotations

import json
from bisect import bisect_right
from collections import defaultdict
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from bp import config
from bp.calendar import business_days
from bp.models import (
    Account, BankTransfer, CorporateAction, CustodianCashActivity, CustodianSettlement, Execution, FeeSchedule,
    LedgerTxn, Order, Person, Price, SymbolRef,
)
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
        self.accounts: list[Account] = _load(world_dir / "accounts.jsonl", Account)
        self.symbols: list[SymbolRef] = _load(world_dir / "symbols.jsonl", SymbolRef)
        self.prices: list[Price] = _load(world_dir / "prices.jsonl", Price)
        self.corporate_actions: list[CorporateAction] = _load(world_dir / "corporate_actions.jsonl", CorporateAction)
        self.fee_schedules: list[FeeSchedule] = _load(world_dir / "fee_schedules.jsonl", FeeSchedule)
        self.people: list[Person] = _load(world_dir / "people.jsonl", Person)
        self.orders: list[Order] = _load(world_dir / "orders.jsonl", Order)
        self.executions: list[Execution] = _load(world_dir / "executions.jsonl", Execution)
        self.bank_transfers: list[BankTransfer] = _load(world_dir / "bank_transfers.jsonl", BankTransfer)
        self.settlements: list[CustodianSettlement] = _load(world_dir / "custodian_settlements.jsonl", CustodianSettlement)
        self.cash_activity: list[CustodianCashActivity] = _load(world_dir / "custodian_cash_activity.jsonl", CustodianCashActivity)
        self.position_activity: list[dict] = _load(world_dir / "custodian_position_activity.jsonl", None)
        self.booking: list[LedgerTxn] = _load(world_dir / "booking.jsonl", LedgerTxn)
        self.truth: list[LedgerTxn] = _load(world_dir / "truth.jsonl", LedgerTxn)
        self.answer_key: list[dict] = _load(world_dir / "answer_key.jsonl", None)
        self.injected: list[str] = []
        self._reindex()

    # ------------------------------------------------------------- indexes
    def _reindex(self) -> None:
        self.order_by_id = {o.order_id: o for o in self.orders}
        self.orders_by_day = defaultdict(list)
        for o in self.orders:
            self.orders_by_day[o.trade_date].append(o)
        self.execs_by_order = defaultdict(list)
        self.execs_by_day = defaultdict(list)
        for e in self.executions:
            self.execs_by_order[e.order_id].append(e)
            self.execs_by_day[e.trade_date].append(e)
        self.booking_by_day = defaultdict(list)
        for t in self.booking:
            self.booking_by_day[t.date].append(t)
        self.settle_by_day = defaultdict(list)
        self.settle_by_ref = {}
        for st in self.settlements:
            self.settle_by_day[st.settle_date].append(st)
            self.settle_by_ref[st.trade_ref] = st
        self.px = {(p.date, p.symbol): p.close_px for p in self.prices}
        self.tier = {a.account_id: a.tier for a in self.accounts}
        self._snapshots()

    def _snapshots(self) -> None:
        """Custodian end-of-day statements: cumulative positions and cash per business day."""
        days = business_days()
        pos_delta = defaultdict(lambda: defaultdict(Decimal))
        for st in self.settlements:
            pos_delta[st.settle_date][(st.account_id, st.symbol)] += st.qty if st.side == "buy" else -st.qty
        for pa in self.position_activity:
            pos_delta[date.fromisoformat(pa["date"])][(pa["account_id"], pa["symbol"])] += D(pa["qty"])
        cash_delta = defaultdict(lambda: defaultdict(Decimal))
        self.cash_by_day = defaultdict(list)
        for ca in self.cash_activity:
            cash_delta[ca.date][ca.account_id] += ca.amount
            self.cash_by_day[ca.date].append(ca)
        self.pos_snap: dict[date, dict] = {}
        self.cash_snap: dict[date, dict] = {}
        pos: dict = defaultdict(Decimal)
        cash: dict = defaultdict(Decimal)
        for d in days:
            for k, v in pos_delta.get(d, {}).items():
                pos[k] += v
            for k, v in cash_delta.get(d, {}).items():
                cash[k] += v
            self.pos_snap[d] = {k: v for k, v in pos.items() if v != 0}
            self.cash_snap[d] = dict(cash)
        self._days = list(days)

    def _asof(self, d: date) -> date | None:
        i = bisect_right(self._days, d)
        return self._days[i - 1] if i else None

    # ------------------------------------------------------------- 1. OMS
    def oms_orders(self, on: date) -> list[Order]:
        return self.orders_by_day.get(on, [])

    # ------------------------------------------------------------- 2. executions
    def broker_executions(self, on: date) -> list[Execution]:
        return self.execs_by_day.get(on, [])

    def executions_for(self, order_id: str) -> list[Execution]:
        return self.execs_by_order.get(order_id, [])

    # ------------------------------------------------------------- 3. booking feed
    def booking_feed(self, on: date) -> list[LedgerTxn]:
        return self.booking_by_day.get(on, [])

    # ------------------------------------------------------------- 4. custodian
    def custodian_settlements(self, on: date) -> list[CustodianSettlement]:
        return self.settle_by_day.get(on, [])

    def custodian_positions(self, asof: date) -> dict[tuple[str, str], Decimal]:
        d = self._asof(asof)
        return self.pos_snap.get(d, {}) if d else {}

    def custodian_cash(self, asof: date) -> dict[str, Decimal]:
        d = self._asof(asof)
        return self.cash_snap.get(d, {}) if d else {}

    def custodian_cash_activity(self, on: date) -> list[CustodianCashActivity]:
        return self.cash_by_day.get(on, [])

    # ------------------------------------------------------------- 5. bank
    def bank_transfers_asof(self, asof: date) -> list[BankTransfer]:
        out = []
        for t in self.bank_transfers:
            if t.initiated_on > asof:
                continue
            if t.status == "returned" and t.status_date > asof:
                t = t.model_copy(update={"status": "settled", "status_date": t.initiated_on, "return_code": None})
            out.append(t)
        return out

    # ------------------------------------------------------------- 6. reference
    def price(self, d: date, symbol: str) -> Decimal:
        dd = self._asof(d)
        return self.px[(dd, symbol)]

    def corporate_actions_asof(self, asof: date) -> list[CorporateAction]:
        return [c for c in self.corporate_actions if c.ex_date <= asof]

    # ------------------------------------------------------------- 7. fee schedule
    def fee_schedule_on(self, d: date) -> FeeSchedule:
        return [s for s in self.fee_schedules if s.effective_from <= d][-1]

    def fee_schedule_before(self, d: date) -> FeeSchedule | None:
        cur = self.fee_schedule_on(d)
        prior = [s for s in self.fee_schedules if s.version < cur.version]
        return prior[-1] if prior else None

    # ------------------------------------------------------------- 8. directory
    def person(self, role: str) -> Person:
        return next(p for p in self.people if p.role == role)

    # ------------------------------------------------------------- live injection overlay
    def apply_injection(self, inj_id: str, records: dict) -> None:
        if inj_id in self.injected:
            return
        self.injected.append(inj_id)
        self.orders += [Order(**x) for x in records.get("orders", [])]
        self.executions += [Execution(**x) for x in records.get("executions", [])]
        self.settlements += [CustodianSettlement(**x) for x in records.get("settlements", [])]
        self.cash_activity += [CustodianCashActivity(**x) for x in records.get("cash_activity", [])]
        self.booking += [LedgerTxn(**x) for x in records.get("booking", [])]
        self.truth += [LedgerTxn(**x) for x in records.get("truth", [])]
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
