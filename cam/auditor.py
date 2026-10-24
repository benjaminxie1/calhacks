"""Bill auditor. Pure code: recomputes the tenant's bill under the lease terms
and attributes each dollar of difference to one finding. No model decides a
number here. A finding resting on an unverified clause, or on a line the
classifier called ambiguous, goes to the owner instead of the letter."""
from __future__ import annotations

from .models import AuditResult, Category, Finding, LeaseTerms, LineClass, Statement, TaxRecord


def _r(x: float) -> float:
    return round(x + 1e-9, 2)


def audit(stmt: Statement, terms: LeaseTerms, classes: list[LineClass], tax: TaxRecord | None,
          verified: dict[str, bool], owner_rulings: dict[str, str] | None = None) -> AuditResult:
    owner_rulings = owner_rulings or {}
    findings: list[Finding] = []
    pending: list[str] = []

    def conf(clause_name: str, base: str = "high") -> str:
        return base if verified.get(clause_name) else "low"

    share = terms.tenant_share_pct or 100 * terms.premises_sf / terms.center_gla_sf
    s = share / 100
    billed_s = stmt.tenant_share_billed_pct / 100
    lines = {l.id: l for l in stmt.lines}
    building_billed = sum(l.amount for l in stmt.lines)
    billed_tenant_total = building_billed * billed_s

    # 1. share
    if abs(stmt.tenant_share_billed_pct - share) > 0.005:
        f_amt = building_billed * (billed_s - s)
        findings.append(Finding(
            rule="share", title=f"Wrong tenant share: billed {stmt.tenant_share_billed_pct:.2f}%, lease says {share:.2f}%",
            tenant_overcharge=_r(f_amt), confidence=conf("share_clause"), clause=terms.share_clause,
            evidence=[f"Premises {terms.premises_sf:,.0f} sf / Shopping Center {terms.center_gla_sf:,.0f} sf"],
            explanation="The lease divides by the whole center's leasable area whether or not it is leased. "
                        "A higher percentage usually means the landlord divided by occupied space only."))

    # 2. capital exclusions and ambiguous repair lines
    excluded_ids: set[str] = set()
    for c in classes:
        line = lines[c.line_id]
        treatment = owner_rulings.get(c.line_id, c.treatment)
        if treatment == "exclude_capital":
            excluded_ids.add(c.line_id)
            findings.append(Finding(
                rule="capital_exclusion", title=f"Excluded capital cost passed through: {line.description}",
                line_ids=[c.line_id], tenant_overcharge=_r(line.amount * s),
                confidence=conf("capital_exclusion_clause"), clause=c.clause,
                evidence=[f"{c.line_id} {line.description}: ${line.amount:,.2f}"],
                explanation=c.reason + (" (owner ruling)" if c.line_id in owner_rulings else "")))
        elif treatment == "ambiguous":
            excluded_ids.add(c.line_id)  # held out of the corrected total until the owner decides
            pending.append(c.line_id)
            findings.append(Finding(
                rule="capital_ambiguous", title=f"Needs your call: {line.description}",
                line_ids=[c.line_id], tenant_overcharge=_r(line.amount * s), confidence="low",
                clause=c.clause, evidence=[f"{c.line_id} {line.description}: ${line.amount:,.2f}"],
                explanation=c.reason, needs_owner=True,
                owner_question=f"Should we dispute {line.description} (your share ${line.amount * s:,.2f}), "
                               "or ask the landlord for the invoice and savings estimate first?"))

    # 3. controllable cap
    controllable = sum(l.amount for l in stmt.lines if l.category == Category.controllable and l.id not in excluded_ids)
    allowed_controllable = controllable
    if terms.controllable_cap_pct is not None:
        cap = stmt.prior_year_controllable_total * (1 + terms.controllable_cap_pct / 100)
        if controllable > cap:
            allowed_controllable = cap
            findings.append(Finding(
                rule="controllable_cap",
                title=f"Controllable expenses rose {100 * (controllable / stmt.prior_year_controllable_total - 1):.1f}%, "
                      f"cap is {terms.controllable_cap_pct:g}%",
                line_ids=[l.id for l in stmt.lines if l.category == Category.controllable],
                tenant_overcharge=_r((controllable - cap) * s), confidence=conf("cap_clause"), clause=terms.cap_clause,
                evidence=[f"Prior year ${stmt.prior_year_controllable_total:,.2f}; cap ${cap:,.2f}; billed ${controllable:,.2f}"],
                explanation="Landscaping, parking, security, repairs and janitorial count as controllable under the lease."))

    # 4. taxes against the county record
    tax_lines = [l for l in stmt.lines if l.category == Category.taxes]
    billed_tax = sum(l.amount for l in tax_lines)
    allowed_tax = billed_tax
    if tax and terms.taxes_actual_paid and billed_tax > tax.total_tax_billed + 1:
        allowed_tax = tax.total_tax_billed
        src = "live county record via Apify" if tax.source == "apify" else "cached county record"
        findings.append(Finding(
            rule="tax_mismatch",
            title=f"Property tax billed ${billed_tax:,.0f}, county bill shows ${tax.total_tax_billed:,.0f}",
            line_ids=[l.id for l in tax_lines], tenant_overcharge=_r((billed_tax - tax.total_tax_billed) * s),
            confidence="medium" if verified.get("tax_clause") else "low", clause=terms.tax_clause,
            evidence=[f"{tax.county} County, APN {tax.apn}, tax year {tax.tax_year}: ${tax.total_tax_billed:,.2f} ({src})"]
                     + ([tax.url] if tax.url else []),
            explanation="The lease passes through only taxes actually levied and paid. A supplemental assessment "
                        "could explain part of the difference, so we ask for the bills rather than assert an error."))

    # 5. admin fee
    fee_lines = [l for l in stmt.lines if l.category == Category.admin_fee]
    billed_fee = sum(l.amount for l in fee_lines)
    allowed_fee = billed_fee
    if terms.admin_fee_max_pct is not None:
        base = allowed_controllable + sum(l.amount for l in stmt.lines
                                          if l.category == Category.repairs and l.id not in excluded_ids)
        excl = {e.lower() for e in terms.admin_fee_base_excludes}
        if "utilities" not in excl:
            base += sum(l.amount for l in stmt.lines if l.category == Category.utilities)
        if "insurance" not in excl:
            base += sum(l.amount for l in stmt.lines if l.category == Category.insurance)
        if "taxes" not in excl:
            base += allowed_tax
        max_fee = base * terms.admin_fee_max_pct / 100
        if billed_fee > max_fee + 1:
            allowed_fee = max_fee
            findings.append(Finding(
                rule="admin_fee", title=f"Management fee ${billed_fee:,.0f} exceeds the lease maximum ${max_fee:,.0f}",
                line_ids=[l.id for l in fee_lines], tenant_overcharge=_r((billed_fee - max_fee) * s),
                confidence=conf("admin_fee_clause"), clause=terms.admin_fee_clause,
                evidence=[f"Lease max {terms.admin_fee_max_pct:g}% of ${base:,.2f} (excluding "
                          f"{', '.join(sorted(excl)) or 'nothing'})"],
                explanation="The fee was charged on taxes, insurance and the capital items too."))

    corrected_building = (allowed_controllable
                          + sum(l.amount for l in stmt.lines if l.category in (Category.utilities, Category.insurance))
                          + sum(l.amount for l in stmt.lines if l.category == Category.repairs and l.id not in excluded_ids)
                          + allowed_tax + allowed_fee)
    correct_tenant_total = corrected_building * s

    # owner must also confirm anything resting on a clause we could not find verbatim
    for f in findings:
        if f.confidence == "low" and not f.needs_owner:
            f.needs_owner = True
            f.owner_question = "We couldn't match the lease clause for this word for word. Please confirm before we send it."

    return AuditResult(
        billed_tenant_total=_r(billed_tenant_total), correct_tenant_total=_r(correct_tenant_total),
        estimates_paid=stmt.estimates_paid, landlord_says_due=_r(billed_tenant_total - stmt.estimates_paid),
        actually_due=_r(correct_tenant_total - stmt.estimates_paid), findings=findings, pending_lines=pending)
