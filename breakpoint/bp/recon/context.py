"""Context step: deterministic code gathers the facts a playbook may branch on.

Branch conditions read only these facts, so the same case always takes the same
branch (R19). Facts are Decimals, bools or short strings.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from bp import cam
from bp.money import D
from bp.recon import matcher as M

FACTS = {
    M.K_SHARE: ["billed_share_pct", "lease_share_pct", "share_by_occupied_pct", "billed_uses_occupied_area"],
    M.K_FEE: ["billed_fee", "lease_max_fee", "lease_fee_pct", "lease_has_fee_clause", "fee_exceeds_lease_max"],
    M.K_CAP: ["controllable_billed", "cap_limit", "cap_pct", "cap_adjustment_billed", "cap_adjustment_missing"],
    M.K_CAPITAL: ["line_amount", "lease_excludes_capital", "replacement_or_structural"],
    M.K_TAX: ["billed_tax", "county_tax", "county_record_found", "lease_taxes_actually_paid"],
    M.K_EST: ["estimates_credited", "estimates_cleared", "shortfall", "monthly_estimate", "all_payments_cleared",
              "shortfall_is_whole_payments"],
    M.K_GROSS: ["gross_up_billed", "gross_up_allowed", "occupancy_pct", "lease_gross_up_pct", "gross_up_not_in_lease"],
}


def facts_for(conn, matcher: M.Matcher, b: dict, d: date) -> dict:
    key = b["break_key"]
    sid, fkey = M.finding_key(b["detection_key"])
    v = matcher.view(sid)
    st, lease, tax = v["statement"], v["lease"], v["tax"]
    lines = st.lines
    if key == M.K_SHARE:
        by_occ = (lease.premises_sf * D(100) / st.occupied_sf).quantize(D("0.01"))
        return {"billed_share_pct": st.share_billed_pct, "lease_share_pct": lease.share_pct,
                "share_by_occupied_pct": by_occ, "billed_uses_occupied_area": st.share_billed_pct == by_occ}
    if key == M.K_FEE:
        tax_basis = tax.total_tax if tax and lease.taxes_actual_paid else None
        mx = cam.allowed_fee(lease, lines, tax_basis, st.prior_year_controllable)
        billed = cam.line_sum(lines, "admin_fee")
        return {"billed_fee": billed, "lease_max_fee": mx, "lease_fee_pct": lease.admin_fee_pct,
                "lease_has_fee_clause": lease.admin_fee_pct is not None, "fee_exceeds_lease_max": billed > mx}
    if key == M.K_CAP:
        adj = cam.line_sum(lines, "cap_adjustment")
        return {"controllable_billed": cam.controllable(lines),
                "cap_limit": cam.cap_limit(st.prior_year_controllable, lease.cap_pct) if lease.cap_pct is not None else None,
                "cap_pct": lease.cap_pct, "cap_adjustment_billed": adj, "cap_adjustment_missing": adj == 0}
    if key == M.K_CAPITAL:
        line = next(l for l in lines if l.line_id == fkey.split(":")[1])
        return {"line_amount": line.amount, "lease_excludes_capital": lease.capital_excluded,
                "replacement_or_structural": cam.is_capital(line)}
    if key == M.K_TAX:
        return {"billed_tax": cam.line_sum(lines, "taxes"), "county_tax": tax.total_tax if tax else None,
                "county_record_found": tax is not None, "lease_taxes_actually_paid": lease.taxes_actual_paid}
    if key == M.K_EST:
        short = v["cleared"] - st.estimates_credited
        monthly = lease.monthly_estimate_2025
        pays = matcher.src("ap").estimate_payments(lease.lease_id, st.year)
        cleared = [p for p in pays if (x := matcher.src("bank").bank_status(p.payment_id)) and x.status == "cleared"]
        return {"estimates_credited": st.estimates_credited, "estimates_cleared": v["cleared"], "shortfall": short,
                "monthly_estimate": monthly, "all_payments_cleared": len(cleared) == len(pays),
                "shortfall_is_whole_payments": monthly > 0 and short % monthly == 0}
    if key == M.K_GROSS:
        return {"gross_up_billed": cam.line_sum(lines, "gross_up"),
                "gross_up_allowed": cam.allowed_gross_up(lease, lines, st.occupancy_pct),
                "occupancy_pct": st.occupancy_pct, "lease_gross_up_pct": lease.gross_up_pct,
                "gross_up_not_in_lease": lease.gross_up_pct is None}
    return {}


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
