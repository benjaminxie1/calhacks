"""Records shared by the stream, the case file and the agents.

Every record has a typed ID ("txn:T00042", "acct:A0007", "party:P0007",
"wl:FWL-03", "rfi:RFI-001"). Findings cite these IDs. The case file rejects a
citation that doesn't resolve, or that the citing agent was never shown.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Stance(str, Enum):
    SUSPICIOUS = "suspicious"
    BENIGN = "benign"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Party:
    id: str
    name: str
    country: str
    kind: str = "person"  # person | business
    dob: str | None = None  # KYC only; an external party's DOB arrives only through an RFI
    owner: str | None = None  # beneficial owner (party id) for businesses


@dataclass(frozen=True)
class Account:
    id: str
    party: str
    opened: datetime
    internal: bool = True


@dataclass(frozen=True)
class Txn:
    id: str
    ts: datetime
    src: str
    dst: str
    amount: float
    channel: str  # wire | ach | p2p | card | cash
    device: str | None = None
    geo: str | None = None
    memo: str = ""


@dataclass(frozen=True)
class WatchlistEntry:
    id: str
    name: str
    aliases: tuple[str, ...]
    dob: str
    country: str
    program: str
    listed_on: datetime


@dataclass(frozen=True)
class RfiResponse:
    """A correspondent bank's free-text reply about an external party. Untrusted text."""
    id: str
    party: str
    text: str
    source: str = "simulated correspondent-bank response"


@dataclass(frozen=True)
class Ownership:
    """One registry line: `owner` holds `pct` of `owned`."""
    id: str
    owner: str
    owned: str
    pct: float
    source: str = "simulated corporate registry"


@dataclass(frozen=True)
class Alert:
    id: str
    rule: str
    subject: str  # account id
    trigger: tuple[str, ...]  # txn ids
    ts: datetime
    detail: str


@dataclass(frozen=True)
class Draft:
    """What a specialist submits. The case file assigns id, agent, round and supersedes."""
    claim: str
    stance: Stance
    typology: str | None
    severity: float  # how bad if true, 0..1
    confidence: float  # 0..1
    evidence: tuple[str, ...]
    facts: dict[str, Any] = field(default_factory=dict)
    exculpatory: bool = False  # a benign finding that explains the activity, not just "nothing in my scope"
    confirmed: bool | None = None  # False: a match that still needs secondary identifiers
    note: str = ""  # the agent's prose; the synthesizer and other agents never read it


@dataclass(frozen=True)
class Finding:
    id: str
    agent: str
    round: int
    claim: str
    stance: Stance
    typology: str | None
    severity: float
    confidence: float
    evidence: tuple[str, ...]
    facts: dict[str, Any]
    exculpatory: bool
    confirmed: bool | None
    supersedes: str | None
    note: str

    @property
    def weight(self) -> float:
        return self.severity * self.confidence if self.stance is Stance.SUSPICIOUS else 0.0

    def public(self) -> dict[str, Any]:
        """The structured projection other agents and the synthesizer see. No prose."""
        return {
            "id": self.id, "agent": self.agent, "round": self.round, "claim": self.claim,
            "stance": self.stance.value, "typology": self.typology, "severity": self.severity,
            "confidence": self.confidence, "exculpatory": self.exculpatory,
            "confirmed": self.confirmed, "supersedes": self.supersedes,
            "evidence": list(self.evidence), "facts": dict(self.facts),
        }


@dataclass(frozen=True)
class Rejection:
    agent: str
    action: str
    reason: str


@dataclass(frozen=True)
class Exchange:
    """One step of the resolution protocol, as logged in the case file."""
    round: int
    agent: str
    challenged_by: tuple[str, ...]  # finding ids the agent was asked to answer
    outcome: str  # revised | maintained | rejected
    new_finding: str | None
    reason: str


@dataclass(frozen=True)
class Recommendation:
    action: str
    target: str
    reason: str
    cites: tuple[str, ...]
    requires: str = "human_analyst"
    status: str = "proposed"  # nothing in this package can change it


@dataclass(frozen=True)
class Truth:
    """Hidden label for a scenario. Only the bench reads it."""
    scenario: str
    suspicious: bool
    typology: str | None
    expected: str  # the disposition a careful analyst would reach on this evidence
    note: str = ""
