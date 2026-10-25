"""Deterministic calculators, one per journal-entry template (R11).

Each calculator is a pure function of its captured inputs. A proposal records the
calculator name and inputs; anyone can recompute it, and a proposal whose amounts
do not match a recomputation is rejected. No other code path produces an amount.

A CAM credit reduces what the tenant owes the landlord and the CAM expense it booked:
debit the lease's landlord payable, credit its CAM expense. An estimates credit
relieves prepaid estimates instead of expense.
"""
from __future__ import annotations

from decimal import Decimal

from bp.cam import cap_limit, share_of
from bp.money import D, cents


class CalculatorMismatch(ValueError):
    pass


def _acct(lease_id: str, kind: str) -> str:
    return f"lease:{lease_id}:{kind}"


def _credit(lease_id: str, amount: Decimal, against: str = "expense") -> list[dict]:
    amt = D(amount)
    if amt == 0:
        return []
    return [{"account": _acct(lease_id, "payable"), "asset": "USD", "amount": amt},
            {"account": _acct(lease_id, against), "asset": "USD", "amount": -amt}]


def share_credit(i: dict) -> dict:
    """Billed share vs lease share, line by line, on the lines as billed."""
    per_line = {l["line_id"]: share_of(l["amount"], i["billed_pct"]) - share_of(l["amount"], i["lease_pct"])
                for l in i["lines"]}
    amt = sum(per_line.values(), D(0))
    return {"postings": _credit(i["lease_id"], amt), "amount_usd": amt,
            "info": {"credit": amt, "billed_pct": D(i["billed_pct"]), "lease_pct": D(i["lease_pct"])}}


def capital_credit(i: dict) -> dict:
    amt = share_of(i["amount"], i["share_pct"])
    return {"postings": _credit(i["lease_id"], amt), "amount_usd": amt,
            "info": {"credit": amt, "line_id": i["line_id"]}}


def cap_credit(i: dict) -> dict:
    limit = cap_limit(i["prior"], i["cap_pct"])
    excess = D(i["controllable"]) - limit
    amt = share_of(excess, i["share_pct"]) + share_of(i["billed_adjustment"], i["share_pct"])
    return {"postings": _credit(i["lease_id"], amt), "amount_usd": amt,
            "info": {"credit": amt, "cap_limit": limit, "excess": excess}}


def fee_credit(i: dict) -> dict:
    allowed = cents(D(i["base"]) * D(i["fee_pct"]) / D(100))
    amt = share_of(i["billed_fee"], i["share_pct"]) - share_of(allowed, i["share_pct"])
    return {"postings": _credit(i["lease_id"], amt), "amount_usd": amt,
            "info": {"credit": amt, "allowed_fee": allowed}}


def tax_credit(i: dict) -> dict:
    amt = share_of(i["billed_tax"], i["share_pct"]) - share_of(i["county_tax"], i["share_pct"])
    return {"postings": _credit(i["lease_id"], amt), "amount_usd": amt, "info": {"credit": amt}}


def gross_up_credit(i: dict) -> dict:
    amt = share_of(i["billed_gross_up"], i["share_pct"]) - share_of(i["allowed_gross_up"], i["share_pct"])
    return {"postings": _credit(i["lease_id"], amt), "amount_usd": amt, "info": {"credit": amt}}


def estimates_credit(i: dict) -> dict:
    amt = D(i["paid"]) - D(i["credited"])
    return {"postings": _credit(i["lease_id"], amt, against="prepaid"), "amount_usd": amt, "info": {"credit": amt}}


CALCULATORS = {
    "share_credit": share_credit, "capital_credit": capital_credit, "cap_credit": cap_credit, "fee_credit": fee_credit,
    "tax_credit": tax_credit, "gross_up_credit": gross_up_credit, "estimates_credit": estimates_credit,
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
