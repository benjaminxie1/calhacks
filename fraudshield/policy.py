"""The disposition policy. Code, not a model: no agent writes a disposition.

Rules are applied in order; the first that matches decides.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .case import SPECIALISTS, CaseFile
from .model import Finding, Recommendation, Stance
from .protocol import CONFLICT_WEIGHT, conflicts

RULES = {
    "R1": "A watchlist match confirmed on secondary identifiers is held, whatever other findings say.",
    "R2": "A potential watchlist match that is neither confirmed nor discounted can't be closed or actioned.",
    "R3": "Specialists still in conflict after resolution: a human decides, with both positions attached.",
    "R4": "Two or more specialists independently suspicious: act, after a human approves.",
    "R5": "One specialist suspicious and nothing to corroborate it: human review.",
    "R6": "An unknown, or a specialist with no accepted finding, blocks a close (it doesn't block action on "
          "other evidence).",
    "R7": "Every specialist benign and at least one explains the activity: close as a false positive.",
    "R8": "Anything else: human review.",
}

ACTIONS = {
    "account_takeover": [("hold_outbound", "subject", "Hold outbound transfers and require step-up re-authentication"),
                         ("contact_customer", "subject", "Call the customer on the number already on file, not one given in this session")],
    "mule": [("restrict_account", "beneficiary", "Restrict outbound payments from the beneficiary account"),
             ("draft_sar", "beneficiary", "Draft a SAR covering the beneficiary and its inbound sources")],
    "structuring": [("draft_sar", "subject", "Draft a SAR for the structuring pattern"),
                    ("no_tipping_off", "subject", "Do not tell the customer a report is being considered")],
    "pass_through": [("enhanced_monitoring", "subject", "Put the account on enhanced monitoring: funds left within days of arriving")],
    "round_trip": [("request_rationale", "subject", "Ask the business for the commercial reason funds return to origin")],
    "unusual_payment": [("confirm_with_customer", "subject", "Confirm the payment with the customer through a known channel")],
    "sanctions": [("hold_payment", "txn", "Hold the payment pending the compliance officer's review"),
                  ("sanctions_report", "party", "Draft a sanctions report for the compliance officer")],
    "sanctions_exposure": [("enhanced_due_diligence", "party",
                            "Enhanced due diligence on the beneficiary's owners before any further payment")],
}


@dataclass
class Decision:
    disposition: str  # close | act | hold_sanctions | review
    rule: str
    cites: tuple[str, ...]
    overruled: tuple[str, ...] = ()
    unresolved: tuple[tuple[str, str], ...] = ()
    recommendations: list[Recommendation] = field(default_factory=list)

    @property
    def rule_text(self) -> str:
        return RULES[self.rule]


def decide(case: CaseFile) -> Decision:
    pos = case.positions()
    ps = list(pos.values())
    ids = tuple(f.id for f in ps)
    pairs = tuple((s.id, b.id) for s, b in conflicts(ps))
    confirmed = [f for f in ps if f.typology == "sanctions" and f.stance is Stance.SUSPICIOUS and f.confirmed]
    if confirmed:
        overruled = tuple(f.id for f in ps if f.stance is Stance.BENIGN and f.exculpatory)
        d = Decision("hold_sanctions", "R1", tuple(f.id for f in confirmed), overruled, pairs)
    elif any(f.confirmed is False for f in ps):
        d = Decision("review", "R2", tuple(f.id for f in ps if f.confirmed is False))
    elif pairs:
        d = Decision("review", "R3", ids, unresolved=pairs)
    else:
        sus = [f for f in ps if f.stance is Stance.SUSPICIOUS and f.weight >= CONFLICT_WEIGHT]
        if len(sus) >= 2:
            d = Decision("act", "R4", tuple(f.id for f in sus))
        elif len(sus) == 1:
            d = Decision("review", "R5", (sus[0].id,))
        elif any(f.stance is Stance.UNKNOWN for f in ps) or set(SPECIALISTS) - set(pos):
            d = Decision("review", "R6", ids)
        elif all(f.stance is Stance.BENIGN for f in ps) and any(f.exculpatory for f in ps):
            d = Decision("close", "R7", ids)
        else:
            d = Decision("review", "R8", ids)
    d.recommendations = _recommend(case, d, ps)
    return d


def _recommend(case: CaseFile, d: Decision, ps: list[Finding]) -> list[Recommendation]:
    if d.disposition == "close":
        return [Recommendation("close_alert", ", ".join(a.id for a in case.alerts),
                               "False positive: every specialist benign; rationale in the positions table", d.cites)]
    if d.disposition == "review":
        why = RULES[d.rule]
        if d.unresolved:
            why += " Conflicts: " + "; ".join(f"{a} vs {b}" for a, b in d.unresolved) + "."
        return [Recommendation("route_to_analyst", case.id, why, d.cites)]
    recs: dict[tuple[str, str], Recommendation] = {}
    for f in sorted((f for f in ps if f.id in d.cites), key=lambda f: -f.weight):
        for action, target_kind, reason in ACTIONS.get(f.typology or "", []):
            target = {"subject": case.subject, "beneficiary": f.facts.get("beneficiary", case.subject),
                      "party": f.facts.get("party", case.subject),
                      "txn": next((e for e in f.evidence if e.startswith("txn:")), case.subject)}[target_kind]
            recs.setdefault((action, target), Recommendation(action, target, reason, (f.id,)))
    return list(recs.values())
