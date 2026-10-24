"""Lease parser agent. Claude reads the lease and returns LeaseTerms with a
verbatim quote for every term. Code then checks each quote against the lease
text; a term whose quote is not in the lease is treated as unverified, and any
finding that depends on it goes to the owner instead of into the letter.

A regex reader is the offline fallback (no API key, tests). Reports say which
reader ran."""
from __future__ import annotations

import re

from .models import Clause, LeaseTerms

MODEL = "claude-opus-5-5"

SYSTEM = """You read commercial retail leases for a small-business tenant.
Extract the cost-sharing terms the tenant needs to audit a year-end CAM (common area
maintenance) reconciliation. For every clause field, copy the exact sentence(s) from the
lease into `quote`, character for character, and give the section number. Never paraphrase
a quote. If the lease does not state a term, return null for it rather than guessing.
Percentages are numbers in percent (7.5 means 7.5%)."""


def _norm(s: str) -> str:
    s = s.replace("**", "").replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def verify_quotes(terms: LeaseTerms, lease_text: str) -> dict[str, bool]:
    """Which clauses appear verbatim (modulo whitespace and bold markers) in the lease."""
    hay = _norm(lease_text)
    out = {}
    for name in ("share_clause", "cap_clause", "capital_exclusion_clause", "admin_fee_clause", "tax_clause", "audit_clause"):
        clause = getattr(terms, name)
        out[name] = clause is not None and len(_norm(clause.quote)) > 15 and _norm(clause.quote) in hay
    return out


def parse_with_claude(lease_text: str) -> LeaseTerms:
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=SYSTEM,
        messages=[{"role": "user", "content": f"<lease>\n{lease_text}\n</lease>"}],
        output_format=LeaseTerms,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to read this lease")
    return response.parsed_output


# --- offline fallback -------------------------------------------------------

def _section(lease_text: str, number: str) -> str | None:
    for para in re.split(r"\n\s*\n", lease_text):
        if re.match(rf"\s*{re.escape(number)}\s", para):
            # drop the "5.4 **Title.**" lead-in so the quote is the operative sentence(s)
            body = re.sub(rf"^\s*{re.escape(number)}\s+\*\*[^*]+\*\*\s*", "", para.strip())
            return body
    return None


def _find_section(lease_text: str, pattern: str) -> tuple[str, str] | None:
    for para in re.split(r"\n\s*\n", lease_text):
        m = re.match(r"\s*(\d+\.\d+)\s", para)
        if m and re.search(pattern, para, re.I):
            return m.group(1), _section(lease_text, m.group(1)) or para
    return None


def _num(pattern: str, text: str) -> float | None:
    m = re.search(pattern, text, re.I | re.S)
    return float(m.group(1).replace(",", "")) if m else None


def parse_with_regex(lease_text: str) -> LeaseTerms:
    def clause(pattern: str) -> Clause | None:
        hit = _find_section(lease_text, pattern)
        return Clause(section=hit[0], quote=hit[1]) if hit else None

    share = clause(r"Tenant's Share")
    cap = clause(r"Controllable")
    excl = clause(r"shall not include")
    fee = clause(r"administrative fee not to exceed")
    tax = clause(r"Real Estate Taxes actually levied")
    audit = clause(r"dispute the statement")

    fee_text = fee.quote if fee else ""
    cap_text = cap.quote if cap else ""
    return LeaseTerms(
        premises_sf=_num(r"containing approximately ([\d,]+) rentable square feet", lease_text) or 0,
        center_gla_sf=_num(r"Gross Leasable Area of the Shopping Center is ([\d,]+)", lease_text) or 0,
        tenant_share_pct=_num(r"percentage is agreed to be ([\d.]+)%", share.quote) if share else None,
        share_clause=share or Clause(section="?", quote=""),
        controllable_cap_pct=_num(r"\(([\d.]+)%\)", cap_text),
        cap_clause=cap,
        controllable_excludes=[k for k in ("insurance", "taxes", "utilities") if k in cap_text.lower()],
        capital_excluded=bool(excl and "capital" in excl.quote.lower()),
        capital_exclusion_clause=excl,
        capital_savings_exception=bool(excl and "reduce operating expenses" in excl.quote.lower()),
        admin_fee_max_pct=_num(r"\(([\d.]+)%\)", fee_text),
        admin_fee_base_excludes=[k for k in ("insurance", "taxes", "capital") if k in fee_text.lower()],
        admin_fee_clause=fee,
        taxes_actual_paid=bool(tax and "actually levied" in tax.quote.lower()),
        tax_clause=tax,
        audit_window_days=int(_num(r"within [a-z\- ]+\((\d+)\) days after receipt", audit.quote) or 0) or None if audit else None,
        audit_clause=audit,
    )


def parse_lease(lease_text: str, reader: str) -> LeaseTerms:
    return parse_with_claude(lease_text) if reader == "claude" else parse_with_regex(lease_text)
