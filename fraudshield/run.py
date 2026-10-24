"""Investigate the alerts in one or all scenarios and write a report per case.

    .venv/bin/python -m fraudshield.run                      # all scenarios, seed 7
    .venv/bin/python -m fraudshield.run --scenario s05_sanctions_match --seed 3
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .monitor import cases
from .report import render_markdown
from .specialists import team
from .stream import SCENARIOS
from .swarm import investigate


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="all", choices=["all", *SCENARIOS])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="runs/fraudshield")
    ap.add_argument("--extractor", default="regex", choices=["regex", "claude"],
                    help="who reads RFI replies; claude needs credentials and falls back to regex without them")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    for name in names:
        world, _truth = SCENARIOS[name](args.seed)  # the truth label is for the bench, not the swarm
        for alerts in cases(world):
            case, d = investigate(world, alerts, team(args.extractor))
            path = out / f"{name}_seed{args.seed}.md"
            path.write_text(render_markdown(case, d, name))
            pos = case.positions()
            print(f"{name:24} {d.disposition:15} {d.rule}  "
                  + "  ".join(f"{r.split('_')[0]}={pos[r].stance.value[:3]}:{pos[r].claim}" for r in pos)
                  + f"  exchanges={len(case.exchanges)} rejections={len(case.rejections)}  -> {path}")


if __name__ == "__main__":
    main()
