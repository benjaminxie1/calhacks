"""Disagreement and resolution.

A conflict is a suspicious finding (weight >= 0.4) against an exculpatory benign one
(confidence >= 0.6) from a different specialist. "Nothing in my scope" is not exculpatory, so a
sanctions no-match never argues with a mule finding. Unconfirmed and unknown findings are open items.

Each round, every agent in a conflict gets the other side's structured claim (never its prose),
and every open item goes back to its author. An agent answers by running its one deeper check;
it may revise only by citing evidence its current finding didn't. A round with no change of stance
ends the protocol. Whatever is still in conflict goes to policy, which escalates or applies a
precedence rule.
"""
from __future__ import annotations

from collections.abc import Iterable

from .case import SPECIALISTS, CaseFile
from .model import Exchange, Finding, Stance

CONFLICT_WEIGHT = 0.4
EXCULPATORY_CONF = 0.6
MAX_ROUNDS = 2


def conflicts(positions: Iterable[Finding]) -> list[tuple[Finding, Finding]]:
    ps = list(positions)
    sus = [f for f in ps if f.stance is Stance.SUSPICIOUS and f.weight >= CONFLICT_WEIGHT]
    exc = [f for f in ps if f.stance is Stance.BENIGN and f.exculpatory and f.confidence >= EXCULPATORY_CONF]
    return [(s, b) for s in sus for b in exc if s.agent != b.agent]


def open_items(positions: Iterable[Finding]) -> list[Finding]:
    return [f for f in positions if f.confirmed is False or f.stance is Stance.UNKNOWN]


def resolve(case: CaseFile, specialists, max_rounds: int = MAX_ROUNDS) -> int:
    """Run resolution rounds. Returns the number of rounds run."""
    by_role = {s.role: s for s in specialists}
    for rnd in range(1, max_rounds + 1):
        pos = case.positions()
        asks: dict[str, list[dict]] = {}
        for s, b in conflicts(pos.values()):
            asks.setdefault(s.agent, []).append(b.public())
            asks.setdefault(b.agent, []).append(s.public())
        for f in open_items(pos.values()):
            asks.setdefault(f.agent, []).append({"id": f.id, "claim": "confirm or resolve"})
        if not asks:
            return rnd - 1
        changed = False
        for role in SPECIALISTS:
            if role not in asks or role not in by_role:
                continue
            before = case.positions()[role]
            ids = tuple(dict.fromkeys(c["id"] for c in asks[role]))
            reply = by_role[role].respond(case, asks[role], rnd)
            if isinstance(reply, str):
                case.log(Exchange(rnd, role, ids, "maintained", None, reply))
                continue
            f = case.submit(role, reply, rnd)
            if f is None:
                case.log(Exchange(rnd, role, ids, "rejected", None, case.rejections[-1].reason))
                continue
            moved = (f.stance, f.confirmed) != (before.stance, before.confirmed)
            changed |= moved
            case.log(Exchange(rnd, role, ids, "revised" if moved else "reaffirmed", f.id,
                              f"{before.claim} -> {f.claim}" if moved else f"{f.claim}, with new evidence"))
        if not changed:
            return rnd
    return max_rounds
