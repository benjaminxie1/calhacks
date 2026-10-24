"""Judge injection: change the sample statement on stage and re-run the audit.

    python -m cam.inject --add "Holiday decorations:4500:controllable"
    python -m cam.inject --add "Parking lot repaving:85000:repairs" --share 7.5
    python -m cam.inject --tax 31000 --fee 0     # a clean-ish bill: should find far less
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import yaml

from .run import SAMPLE, load_env, render, run


def main() -> None:
    load_env()
    p = argparse.ArgumentParser()
    p.add_argument("--add", action="append", default=[], help="description:amount:category")
    p.add_argument("--share", type=float, help="Share the landlord billed, in percent")
    p.add_argument("--tax", type=float, help="Replace the real estate tax line amount")
    p.add_argument("--fee", type=float, help="Replace the management fee amount")
    p.add_argument("--reader", choices=["regex", "claude"], default="regex")
    p.add_argument("--rule", action="append", default=[])
    a = p.parse_args()

    stmt = yaml.safe_load((SAMPLE / "statement.yaml").read_text())
    for i, spec in enumerate(a.add, 1):
        desc, amount, cat = spec.rsplit(":", 2)
        stmt["lines"].append({"id": f"J{i}", "description": desc, "amount": float(amount), "category": cat})
    if a.share is not None:
        stmt["tenant_share_billed_pct"] = a.share
    for line in stmt["lines"]:
        if a.tax is not None and line["category"] == "taxes":
            line["amount"] = a.tax
        if a.fee is not None and line["category"] == "admin_fee":
            line["amount"] = a.fee

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "statement.yaml"
        path.write_text(yaml.safe_dump(stmt))
        r = run(SAMPLE / "lease.md", path, "428-0061-012-03", "Alameda", a.reader,
                "claude" if a.reader == "claude" else "template", dict(x.split("=", 1) for x in a.rule),
                None, SAMPLE / "assessor_cache.json")
    print(render(r))


if __name__ == "__main__":
    main()
