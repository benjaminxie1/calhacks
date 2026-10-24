"""Negotiator: drafts the dispute letter from findings the owner doesn't need to
rule on. Claude writes the prose; code then checks that every dollar figure in
the draft is one the auditor computed, and falls back to the template if not.
Nothing is sent: the letter is a draft for the owner to sign."""
from __future__ import annotations

import re

from .lease_parser import MODEL
from .models import AuditResult, Finding, LeaseTerms, Statement

SYSTEM = """You draft a formal but plain-spoken dispute letter from a small-business tenant to its
landlord about the annual CAM reconciliation. Use only the findings, numbers, and lease sections
given. Do not add any dollar figure that is not in the input. Do not accuse the landlord of fraud;
where a finding has medium confidence, request the supporting documents instead of asserting the
error. Cite the lease section for each point. End by invoking the audit right with its deadline."""


def sendable(result: AuditResult) -> list[Finding]:
    return [f for f in result.findings if not f.needs_owner]


def _money(x: float) -> str:
    return f"${x:,.2f}"


def template_letter(stmt: Statement, terms: LeaseTerms, result: AuditResult) -> str:
    fs = sendable(result)
    total = sum(f.tenant_overcharge for f in fs)
    if result.actually_due < 0:
        outcome = (f"after our estimated payments of {_money(result.estimates_paid)} we are owed a credit of "
                   f"{_money(-result.actually_due)}")
    else:
        outcome = f"{_money(result.actually_due)} is due, not {_money(result.landlord_says_due)}"
    lines = [
        f"To: {stmt.landlord}",
        f"From: {stmt.tenant}",
        f"Re: {stmt.year} Operating Expense and Tax Reconciliation dated {stmt.statement_date}",
        "",
        f"We have reviewed the {stmt.year} reconciliation against our lease and dispute it under "
        f"Section {terms.audit_clause.section if terms.audit_clause else '(audit right)'}. "
        f"The statement bills us {_money(result.billed_tenant_total)} and claims {_money(result.landlord_says_due)} "
        f"is due. Under the lease our share is {_money(result.correct_tenant_total)}, so {outcome}.",
        "",
    ]
    for i, f in enumerate(fs, 1):
        sec = f"Section {f.clause.section}" if f.clause else "the lease"
        ask = (" Please send the tax bills and proof of payment for the year." if f.rule == "tax_mismatch" else "")
        lines.append(f"{i}. {f.title}. Under {sec}, this overstates our share by {_money(f.tenant_overcharge)}. "
                     f"{f.explanation}{ask}")
    lines += ["", f"These items total {_money(total)}."]
    if result.pending_lines:
        names = [l.description for l in stmt.lines if l.id in result.pending_lines]
        lines.append("We reserve the right to dispute other items, including " + "; ".join(names)
                     + ", pending supporting invoices.")
    if terms.audit_window_days:
        lines.append(f"This notice is given within the {terms.audit_window_days}-day period in the lease, and we "
                     "request access to the books and records supporting the statement.")
    lines += ["", "Sincerely,", stmt.tenant]
    return "\n".join(lines)


def allowed_figures(stmt: Statement, terms: LeaseTerms, result: AuditResult) -> set[str]:
    nums = [result.billed_tenant_total, result.correct_tenant_total, result.estimates_paid,
            result.landlord_says_due, result.actually_due, -result.actually_due,
            sum(f.tenant_overcharge for f in sendable(result))]
    nums += [f.tenant_overcharge for f in result.findings]
    nums += [l.amount for l in stmt.lines] + [stmt.prior_year_controllable_total]
    out = set()
    for n in nums:
        out |= {f"{n:,.2f}", f"{n:,.0f}", f"{n:.2f}", f"{n:.0f}"}
    for f in result.findings:
        for text in [f.title, *f.evidence]:
            out |= set(re.findall(r"\d{1,3}(?:,\d{3})+(?:\.\d\d)?|\d+\.\d\d", text))
    return out


def unknown_figures(letter: str, allowed: set[str]) -> list[str]:
    found = re.findall(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d\d)?)", letter)
    return [x for x in found if x not in allowed and x.rstrip("0").rstrip(".") not in allowed]


def claude_letter(stmt: Statement, terms: LeaseTerms, result: AuditResult) -> str:
    import anthropic

    client = anthropic.Anthropic()
    facts = {
        "landlord": stmt.landlord, "tenant": stmt.tenant, "year": stmt.year, "statement_date": stmt.statement_date,
        "billed_tenant_total": result.billed_tenant_total, "correct_tenant_total": result.correct_tenant_total,
        "estimates_paid": result.estimates_paid, "landlord_says_due": result.landlord_says_due,
        "actually_due": result.actually_due, "audit_section": terms.audit_clause.section if terms.audit_clause else None,
        "audit_window_days": terms.audit_window_days, "reserved_lines": result.pending_lines,
        "findings": [f.model_dump(include={"title", "tenant_overcharge", "confidence", "explanation", "evidence"})
                     | {"section": f.clause.section if f.clause else None} for f in sendable(result)],
    }
    with client.messages.stream(
        model=MODEL, max_tokens=16000, thinking={"type": "adaptive"}, output_config={"effort": "medium"},
        system=SYSTEM, messages=[{"role": "user", "content": f"<facts>\n{facts}\n</facts>"}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude declined to draft the letter")
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def draft(stmt: Statement, terms: LeaseTerms, result: AuditResult, writer: str) -> tuple[str, str, list[str]]:
    """Returns (letter, who_wrote_it, figures_rejected)."""
    if writer != "claude":
        return template_letter(stmt, terms, result), "template", []
    letter = claude_letter(stmt, terms, result)
    bad = unknown_figures(letter, allowed_figures(stmt, terms, result))
    if bad:
        return template_letter(stmt, terms, result), "template (Claude draft used figures we didn't compute)", bad
    return letter, f"claude:{MODEL}", []
