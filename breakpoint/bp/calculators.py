"""Deterministic calculators, one per journal-entry template (R11).

Each calculator is a pure function of its captured inputs. A proposal records the
calculator name and inputs; anyone can recompute it, and a proposal whose amounts
do not match a recomputation is rejected. No other code path produces an amount.
"""
from __future__ import annotations

from decimal import Decimal

from bp.money import D, cents


class CalculatorMismatch(ValueError):
    pass


def _client(a: str) -> str:
    return f"client:{a}"


def _pairs(*legs) -> list[dict]:
    """legs: (debit_account, credit_account, asset, amount); zero legs dropped. Each leg balances."""
    out = []
    for acc, offset, asset, amt in legs:
        amt = D(amt)
        if amt != 0:
            out += [{"account": acc, "asset": asset, "amount": amt}, {"account": offset, "asset": asset, "amount": -amt}]
    return out


def reverse_unfilled(i: dict) -> dict:
    """Bring a trade booking back to what actually executed: qty, principal, and fee."""
    sign = D(1) if i["side"] == "buy" else D(-1)
    target_qty = sign * D(i["exec_qty"])
    target_principal = -sign * D(i["exec_principal"])
    dq = target_qty - D(i["ledger_qty"])
    dp = target_principal - D(i["ledger_principal"])
    df = D(i["schedule_fee"]) - D(i["ledger_fee"])
    c = _client(i["account_id"])
    postings = _pairs((c, "street", i["symbol"], dq), (c, "street", "USD", dp), ("revenue:fees", c, "USD", df))
    return {"postings": postings, "amount_usd": abs(dp) + abs(df),
            "info": {"unfilled_qty": abs(dq), "principal_returned": dp, "fee_returned": -df}}


def reverse(i: dict) -> dict:
    """Exact negation of a posted entry (used for duplicates)."""
    postings = [{"account": p["account"], "asset": p["asset"], "amount": -D(p["amount"])} for p in i["postings"]]
    usd = sum((abs(p["amount"]) for p in postings if p["asset"] == "USD" and p["account"].startswith("client:")), D(0))
    return {"postings": postings, "amount_usd": usd, "info": {"reverses": i["txn_id"]}}


def adjust(i: dict) -> dict:
    """Refund (or charge) the difference between the commission charged and the schedule."""
    diff = D(i["charged"]) - D(i["schedule"])
    return {"postings": _pairs((_client(i["account_id"]), "revenue:fees", "USD", diff)), "amount_usd": abs(diff),
            "info": {"refund": diff, "schedule_version": i.get("schedule_version")}}


def dividend(i: dict) -> dict:
    amt = cents(D(i["rate"]) * D(i["shares"]))
    return {"postings": _pairs((_client(i["account_id"]), f"issuer:{i['symbol']}", "USD", amt)), "amount_usd": amt,
            "info": {"dividend": amt}}


def split_adjust(i: dict) -> dict:
    """Multiply each holder's position by the ratio; halve (1/ratio) cost per share; no cash."""
    ratio = D(i["ratio"])
    legs, info = [], {}
    for h in i["holders"]:
        add = D(h["qty"]) * (ratio - 1)
        legs.append((_client(h["account_id"]), f"issuer:{i['symbol']}", i["symbol"], add))
        info[h["account_id"]] = {"old_qty": D(h["qty"]), "new_qty": D(h["qty"]) + add,
                                 "old_cost_per_share": D(h["avg_cost"]),
                                 "new_cost_per_share": cents(D(h["avg_cost"]) / ratio)}
    return {"postings": _pairs(*legs), "amount_usd": D(0), "info": info}


def reverse_deposit(i: dict) -> dict:
    amt = D(i["amount"])
    return {"postings": _pairs(("firm:omnibus", _client(i["account_id"]), "USD", amt)), "amount_usd": amt,
            "info": {"reversed": amt, "return_code": i.get("return_code")}}


def receivable(i: dict) -> dict:
    cash = D(i["cash_after_reversal"])
    deficit = -cash if cash < 0 else D(0)
    c = _client(i["account_id"])
    return {"postings": _pairs((c, f"{c}:receivable", "USD", deficit)), "amount_usd": deficit,
            "info": {"client_owes": deficit}}


CALCULATORS = {
    "reverse_unfilled": reverse_unfilled, "reverse": reverse, "adjust": adjust, "dividend": dividend,
    "split_adjust": split_adjust, "reverse_deposit": reverse_deposit, "receivable": receivable,
}


def run(name: str, inputs: dict) -> dict:
    return CALCULATORS[name](inputs)


def _norm(postings) -> list[tuple]:
    return sorted((p["account"], p["asset"], D(p["amount"])) for p in postings)


def check(name: str, inputs: dict, postings: list[dict], amount_usd: Decimal) -> None:
    """Raise unless the proposal's numbers are exactly what the calculator produces."""
    if name not in CALCULATORS:
        raise CalculatorMismatch(f"no calculator named {name!r}; amounts must come from a calculator")
    out = run(name, inputs)
    if _norm(out["postings"]) != _norm(postings) or D(out["amount_usd"]) != D(amount_usd):
        raise CalculatorMismatch(f"proposal amounts do not match calculator {name}")
