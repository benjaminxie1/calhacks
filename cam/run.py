"""One audit end to end: lease + statement (+ county record) -> findings ->
owner questions -> draft dispute letter.

    python -m cam.run                              # offline readers, sample data
    python -m cam.run --reader claude              # Claude parses, classifies, drafts
    python -m cam.run --rule L9=exclude_capital    # owner answers a pending question
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

import yaml

from . import auditor, classify, evidence, lease_parser, negotiator
from .models import AuditResult, LeaseTerms, Statement
from .trail import Trail

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample"


def load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def run(lease_path: Path, statement_path: Path, apn: str, county: str, reader: str, writer: str,
        rulings: dict[str, str], out_dir: Path | None, tax_cache: Path | None = None) -> dict:
    trail = Trail(out_dir / "trail.jsonl" if out_dir else None)
    lease_text = lease_path.read_text()
    stmt = Statement(**yaml.safe_load(statement_path.read_text()))
    trail.log("intake", "loaded", lease=str(lease_path.name), statement=str(statement_path.name), lines=len(stmt.lines))

    terms: LeaseTerms = lease_parser.parse_lease(lease_text, reader)
    verified = lease_parser.verify_quotes(terms, lease_text)
    trail.log("lease_parser", "extracted", reader=reader, terms=terms.model_dump(exclude={
        "share_clause", "cap_clause", "capital_exclusion_clause", "admin_fee_clause", "tax_clause", "audit_clause"}),
        quotes_verified=verified)

    classes = classify.classify(stmt.lines, terms, reader)
    for c in classes:
        trail.log("classifier", "classified", line=c.line_id, treatment=c.treatment, reason=c.reason)

    tax = evidence.lookup(apn, county, tax_cache)
    trail.log("evidence", "tax_record", source=tax.source if tax else None,
              total=tax.total_tax_billed if tax else None, apn=apn)

    result: AuditResult = auditor.audit(stmt, terms, classes, tax, verified, rulings)
    for f in result.findings:
        trail.log("auditor", "finding", rule=f.rule, amount=f.tenant_overcharge, confidence=f.confidence,
                  needs_owner=f.needs_owner, section=f.clause.section if f.clause else None)

    letter, writer_used, rejected = negotiator.draft(stmt, terms, result, writer)
    trail.log("negotiator", "drafted", writer=writer_used, rejected_figures=rejected,
              withheld=[f.rule for f in result.findings if f.needs_owner])

    report = {"statement": stmt, "terms": terms, "verified": verified, "result": result, "letter": letter,
              "writer": writer_used, "reader": reader, "tax": tax, "trail": trail.events}
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.md").write_text(render(report))
        (out_dir / "letter.txt").write_text(letter)
        (out_dir / "result.json").write_text(result.model_dump_json(indent=2))
    return report


def render(r: dict) -> str:
    res: AuditResult = r["result"]
    stmt: Statement = r["statement"]
    tax = r["tax"]
    out = [f"# CAM audit: {stmt.tenant}, {stmt.year}", "",
           f"Lease read by `{r['reader']}`; letter drafted by `{r['writer']}`; tax record from "
           f"`{tax.source if tax else 'none'}`.", "",
           "| | Amount |", "| --- | --- |",
           f"| Landlord billed your share | ${res.billed_tenant_total:,.2f} |",
           f"| Your share under the lease | ${res.correct_tenant_total:,.2f} |",
           f"| Estimates you paid | ${res.estimates_paid:,.2f} |",
           f"| Landlord says you owe | ${res.landlord_says_due:,.2f} |",
           f"| You actually owe (negative = credit) | ${res.actually_due:,.2f} |", "",
           "## Findings", ""]
    for f in res.findings:
        flag = " **(needs your call)**" if f.needs_owner else ""
        sec = f"Section {f.clause.section}" if f.clause else "no clause"
        out.append(f"- **{f.title}**: ${f.tenant_overcharge:,.2f}, {f.confidence} confidence, {sec}{flag}")
        for e in f.evidence:
            out.append(f"    - Evidence: {e}")
        if f.owner_question:
            out.append(f"    - Question: {f.owner_question}")
    out += ["", "## Lease clauses matched word for word", ""]
    out += [f"- {k}: {'yes' if v else 'NO'}" for k, v in r["verified"].items()]
    out += ["", "## Draft letter (not sent)", "", "```", r["letter"], "```", ""]
    return "\n".join(out)


def main() -> None:
    load_env()
    p = argparse.ArgumentParser()
    p.add_argument("--lease", type=Path, default=SAMPLE / "lease.md")
    p.add_argument("--statement", type=Path, default=SAMPLE / "statement.yaml")
    p.add_argument("--apn", default="428-0061-012-03")
    p.add_argument("--county", default="Alameda")
    p.add_argument("--reader", choices=["regex", "claude"], default="regex")
    p.add_argument("--writer", choices=["template", "claude"], default=None)
    p.add_argument("--rule", action="append", default=[], help="Owner ruling, e.g. L9=exclude_capital or L9=include")
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args()
    rulings = dict(x.split("=", 1) for x in a.rule)
    out = a.out or ROOT / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    r = run(a.lease, a.statement, a.apn, a.county, a.reader, a.writer or ("claude" if a.reader == "claude" else "template"),
            rulings, out, SAMPLE / "assessor_cache.json")
    print(render(r))
    print(f"\nWrote {out}/report.md, letter.txt, result.json, trail.jsonl")


if __name__ == "__main__":
    main()
