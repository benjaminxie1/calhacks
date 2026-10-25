"""Generates the 30 resolved cases from last year's season used for induction (data/history/cases_2025.json).

These are the closed disputes the lease administration team left behind for the 2024
statements (worked March-July 2025): the facts the context step would have gathered,
and how a human resolved each one.
"""
import json
import random
from datetime import date, timedelta
from decimal import Decimal

from bp import config
from bp.recon import matcher as M


def _d(rng):
    return (date(2025, 3, 3) + timedelta(days=rng.randint(0, 130))).isoformat()


def _m(cents: int) -> str:
    return str(Decimal(cents) / 100)


def build(seed: int = 2025) -> list[dict]:
    rng = random.Random(seed)
    cases, n = [], 100
    who = ["Dana Ortiz", "Dana Ortiz", "Dana Ortiz", "Ravi Shah"]

    def add(key, facts, trust, actions, note):
        nonlocal n
        n += 1
        cases.append({"case_id": f"CASE-{n:04d}", "closed_on": _d(rng), "break_key": key,
                      "facts": {k: (str(v) if not isinstance(v, (bool, type(None))) else v) for k, v in facts.items()},
                      "resolution": {"trust": trust, "actions": actions, "resolved_by": who[rng.randrange(4)],
                                     "approved_by": "Sam Lee", "note": note}})

    for _ in range(6):  # share billed on occupied area instead of GLA
        lease = Decimal(rng.randint(450, 1100)) / 100
        occ = Decimal(rng.randint(840, 960)) / 1000
        billed = (lease / occ).quantize(Decimal("0.01"))
        add(M.K_SHARE, {"billed_share_pct": billed, "lease_share_pct": lease, "share_by_occupied_pct": billed,
                        "billed_uses_occupied_area": True},
            "lease", ["share_credit", "dispute_notice"],
            "Landlord divided by leased-and-occupied area; the lease says GLA whether or not occupied. Credited the "
            "difference line by line and sent the dispute notice.")
    for _ in range(6):  # admin fee over the lease limit (charged on taxes, insurance or capital, or at a higher rate)
        mx = rng.randint(800000, 3000000)
        billed = mx + rng.randint(150000, 1500000)
        add(M.K_FEE, {"billed_fee": _m(billed), "lease_max_fee": _m(mx), "lease_fee_pct": [10, 12, 5][rng.randrange(3)],
                      "lease_has_fee_clause": True, "fee_exceeds_lease_max": True},
            "lease", ["fee_credit", "dispute_notice"],
            "Management fee charged on everything, taxes and insurance included. Credited the excess over the lease "
            "maximum and disputed it.")
    for _ in range(5):  # controllable cap not applied
        prior = rng.randint(120000, 260000)
        lim = prior * 105 // 100
        add(M.K_CAP, {"controllable_billed": lim + rng.randint(4000, 20000), "cap_limit": lim, "cap_pct": 5,
                      "cap_adjustment_billed": 0, "cap_adjustment_missing": True},
            "lease", ["cap_credit", "dispute_notice"],
            "Controllable expenses rose more than the cap and the statement had no cap adjustment. Credited the excess.")
    for _ in range(5):  # capital item passed through
        add(M.K_CAPITAL, {"line_amount": rng.randint(30, 120) * 1000, "lease_excludes_capital": True,
                          "replacement_or_structural": True},
            "lease", ["capital_credit", "dispute_notice"],
            "Roof replacement / resurfacing billed as repairs. Capital costs are excluded by the lease. Credited our share.")
    for _ in range(4):  # taxes above the county bill
        county = rng.randint(25000, 70000)
        add(M.K_TAX, {"billed_tax": county * rng.randint(115, 160) // 100, "county_tax": county,
                      "county_record_found": True, "lease_taxes_actually_paid": True},
            "county", ["tax_credit", "request_tax_bills"],
            "Taxes billed above the county's bill for the parcel. Booked to the county figure and asked the landlord for "
            "the tax bills and proof of payment.")
    for _ in range(4):  # estimate payment not credited
        monthly = rng.randint(1500, 3200)
        add(M.K_EST, {"estimates_credited": monthly * 11, "estimates_cleared": monthly * 12, "shortfall": monthly,
                      "monthly_estimate": monthly, "all_payments_cleared": True, "shortfall_is_whole_payments": True},
            "bank", ["estimates_credit", "send_payment_proof"],
            "Landlord credited 11 of 12 estimate payments; all 12 cleared our bank. Credited the missing payment and sent "
            "the bank confirmation.")
    rng.shuffle(cases)
    return cases


if __name__ == "__main__":
    out = config.DATA / "history" / "cases_2025.json"
    out.write_text(json.dumps(build(), indent=1))
    print(f"wrote {out}")
