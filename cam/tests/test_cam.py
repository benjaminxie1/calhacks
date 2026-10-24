from pathlib import Path

import pytest
import yaml

from cam import auditor, classify, lease_parser, negotiator
from cam.evidence import from_cache
from cam.models import Clause, Statement
from cam.run import SAMPLE, run

LEASE = (SAMPLE / "lease.md").read_text()
STMT = Statement(**yaml.safe_load((SAMPLE / "statement.yaml").read_text()))
KEY = yaml.safe_load((SAMPLE / "answer_key.yaml").read_text())
TAX = from_cache(SAMPLE / "assessor_cache.json")


@pytest.fixture
def terms():
    return lease_parser.parse_with_regex(LEASE)


def _audit(terms, **kw):
    verified = kw.pop("verified", None) or lease_parser.verify_quotes(terms, LEASE)
    classes = classify.classify(STMT.lines, terms, "regex")
    return auditor.audit(STMT, terms, classes, kw.pop("tax", TAX), verified, kw.pop("rulings", None))


def test_parser_reads_the_terms(terms):
    assert terms.tenant_share_pct == 7.5
    assert terms.controllable_cap_pct == 5
    assert terms.admin_fee_max_pct == 10
    assert terms.audit_window_days == 90
    assert terms.capital_excluded and terms.capital_savings_exception
    assert all(lease_parser.verify_quotes(terms, LEASE).values())


def test_every_seeded_error_is_found_and_the_total_matches(terms):
    r = _audit(terms)
    assert {f.rule for f in r.findings} >= {e["rule"] for e in KEY["seeded_errors"]}
    assert r.correct_tenant_total == pytest.approx(KEY["expected_tenant_total_excluding_ambiguous"])
    # every dollar of difference is attributed to exactly one finding
    assert sum(f.tenant_overcharge for f in r.findings) == pytest.approx(r.billed_tenant_total - r.correct_tenant_total)


def test_ambiguous_line_goes_to_the_owner_not_the_letter(terms):
    r = _audit(terms)
    hvac = [f for f in r.findings if f.line_ids == ["L9"]]
    assert hvac and hvac[0].needs_owner and r.pending_lines == ["L9"]
    letter = negotiator.template_letter(STMT, terms, r)
    assert "1,050.00" not in letter


def test_owner_ruling_moves_the_line(terms):
    r = _audit(terms, rulings={"L9": "exclude_capital"})
    assert r.pending_lines == []
    assert any(f.rule == "capital_exclusion" and f.line_ids == ["L9"] and not f.needs_owner for f in r.findings)
    kept = _audit(terms, rulings={"L9": "include"})
    assert kept.correct_tenant_total > r.correct_tenant_total


def test_a_clause_not_in_the_lease_cannot_reach_the_letter(terms):
    terms.cap_clause = Clause(section="5.4", quote="Controllable expenses may not rise more than 5% a year.")
    verified = lease_parser.verify_quotes(terms, LEASE)
    assert verified["cap_clause"] is False
    r = _audit(terms, verified=verified)
    cap = next(f for f in r.findings if f.rule == "controllable_cap")
    assert cap.needs_owner and cap not in negotiator.sendable(r)


def test_no_tax_record_means_no_tax_finding(terms):
    r = _audit(terms, tax=None)
    assert "tax_mismatch" not in {f.rule for f in r.findings}


def test_letter_with_an_invented_figure_is_caught(terms):
    r = _audit(terms)
    allowed = negotiator.allowed_figures(STMT, terms, r)
    good = negotiator.template_letter(STMT, terms, r)
    assert negotiator.unknown_figures(good, allowed) == []
    assert negotiator.unknown_figures(good + "\nYou also owe us $9,999.99 in penalties.", allowed) == ["9,999.99"]


def test_clean_statement_produces_no_findings(terms):
    clean = STMT.model_copy(deep=True)
    clean.tenant_share_billed_pct = 7.5
    clean.lines = [l for l in clean.lines if l.id not in ("L8", "L9", "L11")]
    for l in clean.lines:
        if l.id == "L4":
            l.amount -= 16000  # back under the cap
        if l.id == "L10":
            l.amount = 31000
    verified = lease_parser.verify_quotes(terms, LEASE)
    r = auditor.audit(clean, terms, classify.classify(clean.lines, terms, "regex"), TAX, verified)
    assert r.findings == []


def test_end_to_end_writes_the_run(tmp_path: Path):
    r = run(SAMPLE / "lease.md", SAMPLE / "statement.yaml", "428-0061-012-03", "Alameda", "regex", "template",
            {}, tmp_path, SAMPLE / "assessor_cache.json")
    assert (tmp_path / "trail.jsonl").read_text().count("\n") == len(r["trail"])
    assert "credit of $7,072.50" in (tmp_path / "letter.txt").read_text()
