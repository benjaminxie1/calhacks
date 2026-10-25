"""Lease math: what a tenant owes under its lease, and how far a statement is from it.

Pure, deterministic Decimal code shared by the world generator (to build the correct
statement), the matcher (to find findings) and the calculators (to size every credit).
No model ever decides a number here.

Every tenant-level dollar is a per-line share rounded to the cent, the way landlords
print statements. Findings are attributed so they add up exactly: the share finding
is priced on the billed lines (billed share minus lease share, line by line), and every
other finding is priced at the lease share. Their sum is always billed total minus
correct total, whatever order the cases resolve in.
"""
from __future__ import annotations

from decimal import Decimal

from bp.models import Lease, Statement, StatementLine, TaxRecord
from bp.money import D, cents

HUNDRED = D(100)
FEE_BASE_CATEGORIES = ("controllable", "utilities", "insurance", "taxes", "repairs")
CAPITAL_WORDS = ("roof replacement", "replace roof", "structural", "repaving", "resurfacing", "new roof",
                 "parking lot reconstruction", "facade renovation")
VARIABLE_LINES = ("janitorial", "common area utilities")  # what a gross-up clause may scale with occupancy
ROUNDING_TOLERANCE = D("1.00")


def share_of(amount: Decimal, pct: Decimal) -> Decimal:
    return cents(D(amount) * D(pct) / HUNDRED)


def is_capital(line: StatementLine | dict) -> bool:
    desc = (line.description if hasattr(line, "description") else line["description"]).lower()
    return any(w in desc for w in CAPITAL_WORDS)


def cap_limit(prior: Decimal, cap_pct: Decimal) -> Decimal:
    return cents(D(prior) * (HUNDRED + D(cap_pct)) / HUNDRED)


def variable_base(lines) -> Decimal:
    return sum((l.amount for l in lines if l.description.lower() in VARIABLE_LINES), D(0))


def allowed_gross_up(lease: Lease, lines, occupancy_pct: Decimal) -> Decimal:
    if lease.gross_up_pct is None or D(occupancy_pct) >= lease.gross_up_pct:
        return D(0)
    return cents(variable_base(lines) * (lease.gross_up_pct - D(occupancy_pct)) / D(occupancy_pct))


def fee_base(lease: Lease, lines, county_tax: Decimal | None, prior: Decimal) -> Decimal:
    """Operating expenses the admin fee may be charged on: controllable costs after the cap, never capital,
    gross-up or the fee itself, taxes at the county's figure, minus whatever categories the lease excludes."""
    excl = {e.lower() for e in lease.admin_fee_base_excludes}
    base = D(0)
    for l in lines:
        if l.category not in FEE_BASE_CATEGORIES or l.category in excl or is_capital(l):
            continue
        if l.category == "taxes":
            base += D(county_tax) if county_tax is not None else l.amount
        elif l.category != "controllable":
            base += l.amount
    ctl = controllable(lines)
    if lease.cap_pct is not None:
        ctl = min(ctl, cap_limit(prior, lease.cap_pct))
    return base + ctl


def allowed_fee(lease: Lease, lines, county_tax: Decimal | None, prior: Decimal) -> Decimal:
    if lease.admin_fee_pct is None:
        return D(0)
    return cents(fee_base(lease, lines, county_tax, prior) * lease.admin_fee_pct / HUNDRED)


def controllable(lines) -> Decimal:
    return sum((l.amount for l in lines if l.category == "controllable" and not is_capital(l)), D(0))


def line_sum(lines, category: str) -> Decimal:
    return sum((l.amount for l in lines if l.category == category), D(0))


# ------------------------------------------------------------------ findings

def findings(stmt: Statement, lease: Lease, tax: TaxRecord | None, cleared_estimates: Decimal) -> dict[str, dict]:
    """Every way this statement overcharges the tenant under the lease, keyed by finding kind (capital is per line).

    Each finding carries the calculator and inputs that size it, so the matcher, the preparer and
    verify all price it the same way."""
    out: dict[str, dict] = {}
    s, sb = lease.share_pct, stmt.share_billed_pct
    lines = stmt.lines

    if sb > s:
        inputs = {"statement_id": stmt.statement_id, "lease_id": lease.lease_id, "billed_pct": sb, "lease_pct": s,
                  "lines": [{"line_id": l.line_id, "amount": l.amount} for l in lines]}
        out["share"] = {"calculator": "share_credit", "inputs": inputs,
                        "values": {"statement": sb, "lease": s},
                        "reason": f"billed share {sb}% vs lease share {s}% ({lease.clauses['share'].section})"}

    if lease.capital_excluded:
        for l in lines:
            if is_capital(l):
                out[f"capital:{l.line_id}"] = {
                    "calculator": "capital_credit", "line_id": l.line_id,
                    "inputs": {"statement_id": stmt.statement_id, "lease_id": lease.lease_id, "line_id": l.line_id,
                               "description": l.description, "amount": l.amount, "share_pct": s},
                    "values": {"statement": l.amount, "lease": D(0)},
                    "reason": f"{l.line_id} {l.description} ${l.amount:,} is a capital cost the lease excludes"}

    if lease.cap_pct is not None:
        ctl = controllable(lines)
        limit = cap_limit(stmt.prior_year_controllable, lease.cap_pct)
        adj = line_sum(lines, "cap_adjustment")
        if ctl + adj > limit:
            out["cap"] = {"calculator": "cap_credit",
                          "inputs": {"statement_id": stmt.statement_id, "lease_id": lease.lease_id, "controllable": ctl,
                                     "prior": stmt.prior_year_controllable, "cap_pct": lease.cap_pct,
                                     "billed_adjustment": adj, "share_pct": s},
                          "values": {"statement": ctl + adj, "lease": limit},
                          "reason": f"controllable ${ctl:,} vs cap ${limit:,} ({lease.cap_pct}% over prior year)"}

    county = tax.total_tax if tax else None
    billed_tax = line_sum(lines, "taxes")
    if tax and lease.taxes_actual_paid and billed_tax > county:
        out["tax"] = {"calculator": "tax_credit",
                      "inputs": {"statement_id": stmt.statement_id, "lease_id": lease.lease_id, "billed_tax": billed_tax,
                                 "county_tax": county, "apn": tax.apn, "share_pct": s},
                      "values": {"statement": billed_tax, "county": county},
                      "reason": f"real estate taxes ${billed_tax:,} vs county bill ${county:,} (APN {tax.apn})"}

    billed_fee = line_sum(lines, "admin_fee")
    tax_basis = county if lease.taxes_actual_paid else None
    max_fee = allowed_fee(lease, lines, tax_basis, stmt.prior_year_controllable)
    if billed_fee > max_fee:
        out["admin_fee"] = {"calculator": "fee_credit",
                            "inputs": {"statement_id": stmt.statement_id, "lease_id": lease.lease_id,
                                       "billed_fee": billed_fee,
                                       "base": fee_base(lease, lines, tax_basis, stmt.prior_year_controllable),
                                       "fee_pct": lease.admin_fee_pct or D(0), "share_pct": s},
                            "values": {"statement": billed_fee, "lease": max_fee},
                            "reason": f"admin fee ${billed_fee:,} vs lease maximum ${max_fee:,}"}

    gross = line_sum(lines, "gross_up")
    allowed_g = allowed_gross_up(lease, lines, stmt.occupancy_pct)
    if gross > allowed_g:
        out["gross_up"] = {"calculator": "gross_up_credit",
                           "inputs": {"statement_id": stmt.statement_id, "lease_id": lease.lease_id,
                                      "billed_gross_up": gross, "allowed_gross_up": allowed_g, "share_pct": s},
                           "values": {"statement": gross, "lease": allowed_g},
                           "reason": f"gross-up ${gross:,} to occupancy vs ${allowed_g:,} the lease allows"}

    if stmt.estimates_credited < cleared_estimates:
        out["estimates"] = {"calculator": "estimates_credit",
                            "inputs": {"statement_id": stmt.statement_id, "lease_id": lease.lease_id,
                                       "paid": cleared_estimates, "credited": stmt.estimates_credited},
                            "values": {"statement": stmt.estimates_credited, "ap": cleared_estimates},
                            "reason": f"landlord credited ${stmt.estimates_credited:,} of estimates; "
                                      f"${cleared_estimates:,} cleared the bank"}

    printed = stmt.tenant_total
    expected = sum((share_of(l.amount, sb) for l in lines), D(0))
    if printed != expected:
        diff = printed - expected
        out["rounding"] = {"calculator": None, "inputs": {}, "harmless": abs(diff) <= ROUNDING_TOLERANCE,
                           "values": {"statement": printed, "recomputed": expected}, "amount": diff,
                           "reason": (f"printed tenant total differs from line-by-line recomputation by ${abs(diff)} "
                                      f"(within ${ROUNDING_TOLERANCE} tolerance); accepted" if abs(diff) <= ROUNDING_TOLERANCE
                                      else f"printed tenant total off by ${diff}")}
    return out


def correct_lines(lease: Lease, base_lines: list[StatementLine], occupancy_pct: Decimal, prior: Decimal,
                  county_tax: Decimal) -> list[StatementLine]:
    """The statement the lease calls for, from the center's actual operating lines (no fee, cap or gross-up yet)."""
    s = lease.share_pct
    lines = [l.model_copy() for l in base_lines]
    n = len(lines)

    def add(desc, cat, amt):
        nonlocal n
        n += 1
        lines.append(StatementLine(line_id=f"L{n}", description=desc, category=cat, amount=amt,
                                   tenant_amount=share_of(amt, s)))

    g = allowed_gross_up(lease, base_lines, occupancy_pct)
    if g:
        add(f"Gross-up of variable expenses to {lease.gross_up_pct}% occupancy", "gross_up", g)
    if lease.admin_fee_pct is not None:
        add(f"Administrative fee ({lease.admin_fee_pct}%)", "admin_fee", allowed_fee(lease, base_lines, county_tax, prior))
    if lease.cap_pct is not None:
        ctl, limit = controllable(base_lines), cap_limit(prior, lease.cap_pct)
        if ctl > limit:
            add(f"Controllable expense cap ({lease.cap_pct}% over prior year)", "cap_adjustment", limit - ctl)
    for l in lines:
        l.tenant_amount = share_of(l.amount, s)
    return lines
