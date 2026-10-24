"""Generates the ~30 resolved Q2 cases used for induction (data/history/q2_cases.json).

These are the closed tickets the ops team left behind for April-June: the facts the
context step would have gathered, and how a human resolved each one.
"""
import json
import random
from datetime import date, timedelta

from bp import config
from bp.recon import matcher as M


def _d(rng):
    return (date(2026, 4, 1) + timedelta(days=rng.randint(0, 89))).isoformat()


def build(seed: int = 2026) -> list[dict]:
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

    for _ in range(10):  # B1 partial fill booked as full
        q = rng.randint(2, 60) * 10
        e = rng.randint(1, q // 10 - 1) * 10 if q > 10 else 5
        add(M.K_TRADE_QTY, {"ledger_qty": q, "custodian_qty": e, "executions_qty": e, "order_qty": q,
                            "ledger_bookings": 1, "duplicate_exec_report": False, "client_already_notified": False},
            "executions", ["reverse_unfilled", "notify_client_service"],
            f"Order only filled {e} of {q}; booking system booked the full order. Reversed the unfilled shares, cash and fee.")
    for _ in range(6):  # B3 duplicate execution report
        q = rng.randint(1, 40) * 10
        add(M.K_TRADE_QTY, {"ledger_qty": 2 * q, "custodian_qty": q, "executions_qty": q, "order_qty": q,
                            "ledger_bookings": 2, "duplicate_exec_report": True, "client_already_notified": False},
            "custodian", ["request_info_broker", "reverse_duplicate"],
            "Northgate re-sent the execution report (PossDup). Confirmed with the desk, reversed the second booking.")
    for _ in range(8):  # B2 commission at the stale schedule (April 1 repricing)
        new = rng.randint(100, 900)
        old = new + rng.randint(30, 200)
        add(M.K_FEE, {"ledger_fee": f"{old // 100}.{old % 100:02d}", "schedule_fee": f"{new // 100}.{new % 100:02d}",
                      "fee_prior_version": f"{old // 100}.{old % 100:02d}", "schedule_version": 1,
                      "fee_tier_is_premium": False},
            "fee_schedule", ["fee_refund", "notify_client"],
            "Commission engine still on the old schedule after the effective date. Refunded the difference.")
    for _ in range(6):  # B4 dividend not credited
        amt = f"{rng.randint(20, 400)}.{rng.randint(0, 99):02d}"
        add(M.K_CASH, {"cash_shortfall": amt, "dividend_paid": True, "dividend_amount": amt, "ach_return_today": False},
            "custodian", ["post_dividend"],
            "Custodian paid the dividend; our corporate-action job skipped the account. Posted rate x shares on record date.")
    rng.shuffle(cases)
    return cases


if __name__ == "__main__":
    out = config.DATA / "history" / "q2_cases.json"
    out.write_text(json.dumps(build(), indent=1))
    print(f"wrote {out}")
