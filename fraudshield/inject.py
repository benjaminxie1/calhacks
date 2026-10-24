"""A judge types a case; the monitor and the swarm work it live.

Only the payment and a few facts about the payee are given. The customer's history is generated so
the profiler has a baseline. If no monitoring rule fires, the case is opened as a manual referral.

    .venv/bin/python -m fraudshield.inject --payee "Marek Oronsky" --country CY --amount 4800 \
        --rfi-text "Our client Marek Oronsky, born 2 March 1961, holds Belarusian citizenship."
    .venv/bin/python -m fraudshield.inject --payee "Dana Holt" --amount 18000 --channel p2p --new-device \
        --hour 3 --payee-age-days 6 --fan-in 5 --forward 0.95
"""
from __future__ import annotations

import argparse
import random
from datetime import timedelta
from pathlib import Path

from .model import Alert
from .monitor import cases
from .report import render_markdown
from .stream import CITIES, TODAY, Builder, h, routine, town
from .specialists import team
from .swarm import investigate


def build(args: argparse.Namespace):
    b = Builder(args.seed)
    t, rng = town(b), b.rng
    cust = b.account(b.person(), 1500)
    device, geo = routine(b, cust, 400, t)
    internal = args.payee_age_days is not None
    payee = b.account(b.person(args.payee, args.country, internal=internal), args.payee_age_days or 900,
                      internal=internal)
    if args.rfi_text:
        b.rfi[b.accounts[payee].party] = args.rfi_text
    received = args.amount
    for src in rng.sample(t.people, min(args.fan_in, len(t.people))):
        amt = rng.uniform(1500, 9000)
        received += amt
        b.txn(src, payee, amt, TODAY - h(rng.uniform(4, 40)), "p2p", f"dev:{src.split(':')[1]}-phone",
              rng.choice(CITIES))
    when = TODAY + h(args.hour)
    b.txn(cust, payee, args.amount, when, args.channel,
          f"dev:X{rng.randint(100, 999)}" if args.new_device else device,
          "Amsterdam, NL (VPN exit)" if args.new_device else geo, args.memo)
    if args.forward and internal:
        out = b.account(b.business("Offshore Exchange Ltd", "MT"), 2000, internal=False)
        remaining, k = received * args.forward, 0
        while remaining > 1:
            chunk = min(remaining, rng.uniform(6000, 9800))
            b.txn(payee, out, chunk, when + h(0.5 + 0.7 * k), "wire")
            remaining, k = remaining - chunk, k + 1
    return b.world(), cust


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--payee", required=True, help="payee name as it appears on the payment")
    ap.add_argument("--country", default="US", help="payee country on the payment message")
    ap.add_argument("--amount", type=float, required=True)
    ap.add_argument("--channel", default="wire", choices=["wire", "ach", "p2p"])
    ap.add_argument("--memo", default="")
    ap.add_argument("--hour", type=float, default=11.0, help="hour of day the payment is sent")
    ap.add_argument("--new-device", action="store_true", help="sent from a device the customer never used")
    ap.add_argument("--payee-age-days", type=int, help="make the payee an internal account this many days old")
    ap.add_argument("--fan-in", type=int, default=0, help="other accounts that paid the payee in the last 48h")
    ap.add_argument("--forward", type=float, default=0.0,
                    help="share of everything the payee received that it forwards out within hours")
    ap.add_argument("--rfi-text", help="the payee bank's free-text reply, if an RFI is sent")
    ap.add_argument("--extractor", default="regex", choices=["regex", "claude"])
    ap.add_argument("--seed", type=int, default=random.randrange(10_000))
    ap.add_argument("--out", default="runs/fraudshield")
    args = ap.parse_args()

    world, cust = build(args)
    found = [c for c in cases(world) if c[0].subject == cust]
    if found:
        alerts = found[0]
    else:
        trig = max((t for t in world.touching(cust) if t.src == cust), key=lambda t: t.ts)
        alerts = [Alert("alert:AL-MANUAL", "MANUAL_REFERRAL", cust, (trig.id,), trig.ts,
                        "no monitoring rule fired; opened by an analyst")]
    case, d = investigate(world, alerts, team(args.extractor))
    report = render_markdown(case, d, f"judge-injected (seed {args.seed})")
    path = Path(args.out) / f"injected_seed{args.seed}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report)
    print(report)
    print(f"-> {path}")


if __name__ == "__main__":
    main()
