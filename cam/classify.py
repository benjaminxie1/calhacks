"""Decides, line by line, whether a repair-type charge is an ordinary repair the
lease lets the landlord pass through or an excluded capital item. Claude makes
the call with the lease's exclusion clause in front of it and must say
"ambiguous" when the lease and the line description don't settle it. The
keyword reader is the offline fallback."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .lease_parser import MODEL
from .models import BillLine, Category, LeaseTerms, LineClass

SYSTEM = """You help a small-business tenant check a landlord's year-end CAM bill against the lease.
For each bill line given, decide its treatment under the lease's capital-expense exclusion:
- "include": an ordinary repair or operating cost the lease allows.
- "exclude_capital": clearly a capital item the lease excludes.
- "ambiguous": the lease and the line description do not settle it (for example, an exception
  for capital items that reduce operating costs may apply and you cannot tell). Say what document
  would settle it.
Do not guess. A wrong "exclude_capital" goes into a dispute letter under the tenant's name."""


class _Out(BaseModel):
    line_id: str
    treatment: Literal["include", "exclude_capital", "ambiguous"]
    reason: str


class _Batch(BaseModel):
    lines: list[_Out]


CAPITAL_WORDS = ("roof replacement", "structural", "repaving", "resurfacing", "new roof")
SYSTEM_WORDS = ("hvac", "boiler", "chiller", "elevator", "fire alarm", "system")


def _keyword(line: BillLine, terms: LeaseTerms) -> LineClass:
    d = line.description.lower()
    clause = terms.capital_exclusion_clause
    if not terms.capital_excluded:
        return LineClass(line_id=line.id, treatment="include", reason="Lease has no capital exclusion.")
    if any(w in d for w in CAPITAL_WORDS):
        return LineClass(line_id=line.id, treatment="exclude_capital", clause=clause,
                         reason="Replacement of a major building component is a capital cost the lease excludes.")
    if "replace" in d and any(w in d for w in SYSTEM_WORDS):
        if terms.capital_savings_exception:
            return LineClass(line_id=line.id, treatment="ambiguous", clause=clause,
                             reason="Replacing a building system is capital, but the lease lets the landlord include a "
                                    "capital item that reduces operating costs, amortized, up to the savings. The bill "
                                    "doesn't say whether this unit saves money or how it was amortized.")
        return LineClass(line_id=line.id, treatment="exclude_capital", clause=clause,
                         reason="Replacement of a building system is a capital cost the lease excludes.")
    return LineClass(line_id=line.id, treatment="include", reason="Ordinary repair.")


def _claude(lines: list[BillLine], terms: LeaseTerms) -> list[LineClass]:
    import anthropic

    client = anthropic.Anthropic()
    clause = terms.capital_exclusion_clause
    payload = "\n".join(f"{l.id}: {l.description} (${l.amount:,.2f})" for l in lines)
    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=SYSTEM,
        messages=[{"role": "user", "content":
                   f"<exclusion_clause section='{clause.section if clause else '?'}'>\n"
                   f"{clause.quote if clause else 'none'}\n</exclusion_clause>\n<lines>\n{payload}\n</lines>"}],
        output_format=_Batch,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to classify these lines")
    by_id = {o.line_id: o for o in response.parsed_output.lines}
    out = []
    for l in lines:
        o = by_id.get(l.id)
        # a line Claude skipped is not silently included
        out.append(LineClass(line_id=l.id, treatment=o.treatment if o else "ambiguous",
                             reason=o.reason if o else "Not classified; needs review.", clause=clause))
    return out


def classify(lines: list[BillLine], terms: LeaseTerms, reader: str) -> list[LineClass]:
    repair = [l for l in lines if l.category == Category.repairs]
    if not repair:
        return []
    return _claude(repair, terms) if reader == "claude" else [_keyword(l, terms) for l in repair]
