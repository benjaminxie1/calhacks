"""Context step: deterministic code gathers the facts a playbook may branch on.

Branch conditions read only these facts, so the same case always takes the same
branch (R19). Facts are Decimals, bools or short strings.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from bp.money import D
from bp.recon import matcher as M

FACTS = {
    M.K_TRADE_QTY: ["ledger_qty", "custodian_qty", "executions_qty", "order_qty", "ledger_bookings",
                    "duplicate_exec_report", "client_already_notified"],
    M.K_FEE: ["ledger_fee", "schedule_fee", "fee_prior_version", "schedule_version", "fee_tier_is_premium"],
    M.K_CASH: ["cash_shortfall", "dividend_paid", "dividend_amount", "ach_return_today"],
    M.K_POS: ["ledger_qty", "custodian_qty", "qty_diff", "split_on_symbol", "split_ratio", "dividend_on_symbol"],
    M.K_BANK: ["bank_status_returned", "ledger_net", "deposit_amount", "bought_after_deposit",
               "cash_after_reversal"],
}


def facts_for(conn, matcher: M.Matcher, b: dict, d: date) -> dict:
    key = b["break_key"]
    src = matcher.src
    if key == M.K_TRADE_QTY:
        tv = matcher.trade_view(conn, b["ref"])
        _, info = matcher._trade_qty_status(tv)
        return {
            "ledger_qty": info["ledger_qty"], "custodian_qty": info["custodian_qty"],
            "executions_qty": info["executions_qty"], "order_qty": tv["order"].qty,
            "ledger_bookings": D(tv["ledger"]["bookings"]), "duplicate_exec_report": tv["dup_report"],
            "client_already_notified": False,
        }
    if key == M.K_FEE:
        fv = matcher.fee_view(conn, b["ref"])
        prior = sum((x["prior"] for x in fv["bookings"] if x["prior"] is not None), D(0)) if fv["prior_version"] else None
        return {"ledger_fee": fv["net_charged"], "schedule_fee": fv["net_schedule"], "fee_prior_version": prior,
                "schedule_version": D(fv["version"]), "fee_tier_is_premium": fv["tier"] == "premium"}
    if key == M.K_CASH:
        acts = [a for a in src("custodian").custodian_cash_activity(date.fromisoformat(str(b["detected_on"])))
                if a.account_id == b["account_id"]]
        from bp.agents.core import ledger_refs
        booked = ledger_refs(conn, [a.ref for a in acts if a.kind == "dividend"])
        divs = [a for a in acts if a.kind == "dividend" and a.ref not in booked]
        return {
            "cash_shortfall": -D(b["vals"]["unexplained"]),
            "dividend_paid": bool(divs), "dividend_amount": sum((a.amount for a in divs), D(0)) if divs else None,
            "ach_return_today": any(a.kind == "ach_return" for a in acts),
        }
    if key == M.K_POS:
        det = date.fromisoformat(str(b["detected_on"]))
        cas = [c for c in src("reference").corporate_actions_asof(det) if c.symbol == b["symbol"]]
        split = next((c for c in cas if c.type == "split" and c.ex_date == det), None)
        return {
            "ledger_qty": D(b["vals"]["ledger"]), "custodian_qty": D(b["vals"]["custodian"]),
            "qty_diff": D(b["vals"]["custodian"]) - D(b["vals"]["ledger"]),
            "split_on_symbol": split is not None, "split_ratio": split.ratio if split else None,
            "dividend_on_symbol": any(c.type == "dividend" and c.ex_date == det for c in cas),
        }
    if key == M.K_BANK:
        bv = matcher.bank_view(conn, b["ref"], d)
        t = bv["transfer"]
        from bp.ledger import service as ledger

        bal = ledger.client_balances(conn)
        cash_now = bal.get((t.account_id, "USD"), D(0))
        bought = any(o.account_id == t.account_id and o.side == "buy" and o.status != "cancelled"
                     for day in _days(t.initiated_on, d) for o in src("oms").oms_orders(day))
        return {
            "bank_status_returned": t.status == "returned", "ledger_net": bv["ledger_net"],
            "deposit_amount": t.amount if t.direction == "in" else -t.amount, "bought_after_deposit": bought,
            "cash_after_reversal": cash_now - (bv["ledger_net"] - bv["expected"]),
        }
    return {}


def _days(a: date, b: date):
    from bp.calendar import business_days

    return [x for x in business_days() if a <= x <= b]


def to_jsonable(f: dict) -> dict:
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in f.items()}


def from_jsonable(f: dict) -> dict:
    out = {}
    for k, v in f.items():
        if isinstance(v, str):
            try:
                out[k] = Decimal(v)
                continue
            except Exception:
                pass
        out[k] = v
    return out
