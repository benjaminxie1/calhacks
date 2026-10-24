"""Live break injection (R26): a judge picks a type, account and symbol; the break
lands in the next replay day with an answer-key row."""
from __future__ import annotations

from datetime import date

from bp.calendar import next_bd
from bp.models import Posting
from bp.money import D, cents, s
from bp.sources.store import SourceStore
from bp.world.generate import acct_key, compute_fee

TYPES = {"B1": "Partial fill booked as full", "B2": "Commission at the old schedule",
         "B3": "Duplicate execution report", "H2": "Price rounding ($0.01)"}


def build(store: SourceStore, kind: str, account_id: str, symbol: str, on: date, seq: int) -> dict:
    if kind not in TYPES:
        raise ValueError(f"injectable types: {sorted(TYPES)}")
    if account_id not in store.tier:
        raise ValueError(f"unknown account {account_id}")
    tier = store.tier[account_id]
    px = store.price(on, symbol)
    qty = D(100) if px < 200 else D(20)
    exec_qty = qty * D(6) / D(10) if kind == "B1" else qty
    oid, eid, tid = f"ORD-J{seq:04d}", f"EX-J{seq:04d}", f"TXN-J{seq:04d}"
    sched = store.fee_schedule_on(on)
    true_fee = compute_fee(tier, exec_qty, sched)
    gross = cents(exec_qty * px)
    settle = next_bd(on)
    a = acct_key(account_id)

    def txn(q, g, fee, txn_id):
        return {"txn_id": txn_id, "date": on.isoformat(), "settle_date": settle.isoformat(), "kind": "trade",
                "source_ref": oid, "reason": f"buy {s(q)} {symbol}", "actor": "booking-system",
                "postings": [Posting(account=x, asset=y, amount=z).model_dump(mode="json") for x, y, z in [
                    (a, "USD", -g), ("street", "USD", g), (a, symbol, q), ("street", symbol, -q),
                    (a, "USD", -fee), ("revenue:fees", "USD", fee)]]}

    truth = txn(exec_qty, gross, true_fee, tid)
    booking = [truth]
    executions = [{"exec_id": eid, "order_id": oid, "fill_qty": s(exec_qty), "fill_px": s(px), "venue": "NSDQ",
                   "exec_time": f"{on}T10:30:00", "trade_date": on.isoformat(), "exec_type": "new", "poss_dup": False}]
    net = -gross
    fix, key = [], f"trade_qty:{oid}"
    if kind == "B1":
        executions.append({**executions[0], "exec_id": eid + "C", "fill_qty": "0", "fill_px": "0", "exec_type": "cancel"})
        booked_fee = compute_fee(tier, qty, sched)
        booking = [txn(qty, gross + cents((qty - exec_qty) * px), booked_fee, tid)]
        fix = [{"action": "reverse_unfilled", "amounts": {"qty": s(qty - exec_qty), "principal": s(cents((qty - exec_qty) * px)),
                                                         "fee": s(booked_fee - true_fee)}}]
    elif kind == "B2":
        prior = store.fee_schedule_before(on)
        old = compute_fee(tier, qty, prior) if prior else true_fee
        if old == true_fee:
            raise ValueError("no prior schedule differs for this account and date; pick another account or a later date")
        booking = [txn(qty, gross, old, tid)]
        key = f"fee:{oid}"
        fix = [{"action": "fee_refund", "amounts": {"refund": s(old - true_fee)}}]
    elif kind == "B3":
        executions.append({**executions[0], "poss_dup": True})
        booking = [truth, {**truth, "txn_id": tid + "-R"}]
        fix = [{"action": "reverse_duplicate", "amounts": {"txn": tid + "-R"}}]
    elif kind == "H2":
        net = -gross + D("0.01")
        key = f"price:{oid}"
    status = "partially_filled" if kind == "B1" else "filled"
    return {
        "orders": [{"order_id": oid, "account_id": account_id, "symbol": symbol, "side": "buy", "qty": s(qty),
                    "order_type": "market", "limit_px": None, "submitted_at": f"{on}T10:29:00",
                    "trade_date": on.isoformat(), "status": status}],
        "executions": executions,
        "settlements": [{"trade_ref": oid, "account_id": account_id, "symbol": symbol, "side": "buy", "qty": s(exec_qty),
                         "settle_date": settle.isoformat(), "net_amount": s(net), "commission": s(true_fee)}],
        "cash_activity": [{"date": settle.isoformat(), "account_id": account_id, "kind": "settlement", "ref": oid,
                           "amount": s(-gross - true_fee), "symbol": None}],
        "booking": booking, "truth": [truth],
        "answer_key": [{"break_id": f"BRK-LIVE-{seq:03d}", "type": kind, "injected_on": on.isoformat(),
                        "affected": [{"source": "ledger", "id": oid}], "expected_detection_keys": [key],
                        "root_cause_source": "custodian" if kind == "H2" else "ledger", "expected_fix": fix,
                        "expected_final_status": "auto_cleared" if kind == "H2" else "completed",
                        "first_status": None, "note": f"injected live: {TYPES[kind]}"}],
    }
