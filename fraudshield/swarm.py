"""One investigation, end to end: assess, resolve, decide."""
from __future__ import annotations

from .case import CaseFile
from .model import Alert
from .policy import Decision, decide
from .protocol import resolve
from .specialists import DEFAULT
from .stream import World


def investigate(world: World, alerts: list[Alert], specialists=DEFAULT, resolution: bool = True) -> tuple[CaseFile, Decision]:
    case = CaseFile(world, alerts)
    for s in specialists:
        case.submit(s.role, s.assess(case), round=0)
    if resolution:
        resolve(case, specialists)
    return case, decide(case)
