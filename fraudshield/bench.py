"""Score three arms against hidden truth.

  rules_only    every alert goes to an analyst (today's queue)
  majority      round-0 findings, majority vote, no resolution, no precedence rules
  swarm         the full protocol and policy

The scenarios and the specialists were written by the same people, so this measures whether the
protocol behaves as designed on planted patterns. It is not a real-world detection rate.

    .venv/bin/python -m fraudshield.bench --seeds 30
"""
from __future__ import annotations

import argparse
from collections import Counter

from .case import CaseFile
from .model import Stance
from .monitor import cases
from .protocol import CONFLICT_WEIGHT
from .specialists import DEFAULT
from .stream import SCENARIOS
from .swarm import investigate

ACTION = {"act", "hold_sanctions"}


def majority(world, alerts) -> str:
    case = CaseFile(world, alerts)
    for s in DEFAULT:
        case.submit(s.role, s.assess(case))
    ps = case.positions().values()
    sus = sum(f.stance is Stance.SUSPICIOUS and f.weight >= CONFLICT_WEIGHT for f in ps)
    ben = sum(f.stance is Stance.BENIGN for f in ps)
    return "act" if sus > ben else "close" if ben > sus else "review"


def outcome(truth_suspicious: bool, disposition: str) -> str:
    if disposition == "review":
        return "to_analyst"
    if disposition in ACTION:
        return "caught" if truth_suspicious else "false_action"
    return "missed" if truth_suspicious else "cleared"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=30)
    args = ap.parse_args()
    arms = {"rules_only": lambda w, a: "review", "majority": majority,
            "swarm": lambda w, a: investigate(w, a)[1].disposition}
    totals = {arm: Counter() for arm in arms}
    per = {arm: {s: Counter() for s in SCENARIOS} for arm in arms}
    for name, build in SCENARIOS.items():
        for seed in range(args.seeds):
            world, truth = build(seed)
            (alerts,) = cases(world)
            for arm, fn in arms.items():
                disposition = fn(world, alerts)
                o = outcome(truth.suspicious, disposition)
                totals[arm][o] += 1
                totals[arm]["exact"] += disposition == truth.expected
                per[arm][name][o] += 1
    cols = ["caught", "missed", "cleared", "false_action", "to_analyst", "exact"]
    n = args.seeds * len(SCENARIOS)
    print(f"{n} cases ({args.seeds} seeds x {len(SCENARIOS)} scenarios), scored against hidden truth\n")
    print(f"{'arm':12}" + "".join(f"{c:>14}" for c in cols))
    for arm in arms:
        print(f"{arm:12}" + "".join(f"{totals[arm][c]:>14}" for c in cols))
    print("\nexact = the disposition a careful analyst would reach on this evidence (Truth.expected)")
    print("\nper scenario (caught/missed/cleared/false_action/to_analyst):")
    for name in SCENARIOS:
        print(f"  {name:24}" + "".join(f"{arm}={'/'.join(str(per[arm][name][c]) for c in cols[:-1]):<20}"
                                       for arm in arms))


if __name__ == "__main__":
    main()
