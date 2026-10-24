"""The case file: the shared record the agents write to, and the rules they can't argue past.

Rules enforced here, in code (the SpacetimeDB port moves each one into a reducer):
  - Only specialists file findings; only the sanctions checker sends RFIs.
  - Every finding cites evidence; every cited ID exists and was shown to the citing agent.
  - Findings are append-only. A revision is a new finding that supersedes the agent's current
    position, is allowed only in a resolution round, and must cite evidence the old one didn't.
  - A broken rule records a Rejection and returns None. The case keeps going.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from .model import (Account, Alert, Draft, Exchange, Finding, Ownership, Party, Rejection, RfiResponse, Txn,
                    WatchlistEntry)
from .stream import CASH, World

SPECIALISTS = ("graph_analyst", "sanctions_checker", "behavioral_profiler")
GRAPH_DAYS = (30, 400)  # standard, deep
PROFILE_DAYS = (90, 400)


@dataclass
class View:
    """The slice of data one role is shown. Agents read only this."""
    role: str
    deep: bool
    subject: str
    now: datetime
    since: datetime
    trigger: list[Txn]
    txns: list[Txn] = field(default_factory=list)  # graph: edges in the 2-hop window; profiler: subject history
    recent: list[Txn] = field(default_factory=list)  # profiler: subject activity since the first trigger
    accounts: dict[str, Account] = field(default_factory=dict)
    owners: dict[str, str] = field(default_factory=dict)  # party -> beneficial owner (graph)
    parties: dict[str, Party] = field(default_factory=dict)  # sanctions: names and countries
    watchlist: list[WatchlistEntry] = field(default_factory=list)
    ownership: list[Ownership] = field(default_factory=list)  # sanctions: registry chain above counterparties

    def ids(self) -> set[str]:
        out = {t.id for t in (*self.trigger, *self.txns, *self.recent)}
        out |= set(self.accounts) | set(self.parties) | set(self.owners) | set(self.owners.values())
        out |= {w.id for w in self.watchlist} | {o.id for o in self.ownership}
        return out


class CaseFile:
    def __init__(self, world: World, alerts: list[Alert]):
        self.world = world
        self.alerts = alerts
        self.subject = alerts[0].subject
        self.id = f"case:{self.subject.split(':', 1)[1]}"
        self.trigger_ids = tuple(dict.fromkeys(i for a in alerts for i in a.trigger))
        self.trigger = sorted((world.by_id[i] for i in self.trigger_ids), key=lambda t: t.ts)
        self.start = self.trigger[0].ts
        self.findings: list[Finding] = []
        self.rejections: list[Rejection] = []
        self.exchanges: list[Exchange] = []
        self.rfis: dict[str, RfiResponse] = {}
        self._seen: dict[str, set[str]] = {r: set() for r in SPECIALISTS}
        self._deep: set[str] = set()

    # --- reads -------------------------------------------------------------------------------------

    def view(self, role: str, deep: bool = False) -> View:
        if role not in SPECIALISTS:
            raise PermissionError(f"{role} has no data view; it reads structured findings only")
        if deep:
            self._deep.add(role)
        v = {"graph_analyst": self._graph_view, "sanctions_checker": self._sanctions_view,
             "behavioral_profiler": self._profile_view}[role](deep)
        self._seen[role] |= v.ids()
        return v

    def deep_used(self, role: str) -> bool:
        return role in self._deep

    def _graph_view(self, deep: bool) -> View:
        w, since = self.world, self.start - timedelta(days=GRAPH_DAYS[deep])
        inside = lambda t: since <= t.ts <= w.now  # noqa: E731
        # Edges touching the subject or its direct counterparties: two hops of nodes.
        core = {self.subject} | {t.dst if t.src == self.subject else t.src
                                 for t in w.touching(self.subject) if inside(t)}
        edges = {t.id: t for a in core - {CASH} for t in w.touching(a) if inside(t)}
        txns = [replace(t, device=None, geo=None, memo="") for t in sorted(edges.values(), key=lambda t: t.ts)]
        accts = {a: w.accounts[a] for t in txns for a in (t.src, t.dst)}
        owners = {}
        for acct in accts.values():
            party = w.parties[acct.party]
            if party.owner:
                owners[party.id] = party.owner
        trigger = [replace(t, device=None, geo=None, memo="") for t in self.trigger]
        return View("graph_analyst", deep, self.subject, w.now, since, trigger, txns, accounts=accts, owners=owners)

    def _sanctions_view(self, deep: bool) -> View:
        w = self.world
        accts = {self.subject} | {t.dst if t.src == self.subject else t.src for t in self.trigger}
        parties = {}
        for a in accts:
            p = w.parties[w.accounts[a].party]
            if p.kind != "cash":
                parties[p.id] = p
        chain, frontier = [], set(parties)
        for _ in range(3):  # registry owners of the counterparties, three levels up
            up = [o for o in w.ownership if o.owned in frontier and o not in chain]
            chain += up
            frontier = {o.owner for o in up}
            parties.update({pid: w.parties[pid] for pid in frontier})
        listed = [e for e in w.watchlist if e.listed_on <= w.now]
        trigger = [replace(t, device=None, geo=None, memo="") for t in self.trigger]
        return View("sanctions_checker", deep, self.subject, w.now, self.start, trigger,
                    accounts={a: w.accounts[a] for a in accts}, parties=parties, watchlist=listed, ownership=chain)

    def _profile_view(self, deep: bool) -> View:
        w, since = self.world, self.start - timedelta(days=PROFILE_DAYS[deep])
        mine = w.touching(self.subject)
        trigger = set(self.trigger_ids)
        history = [t for t in mine if since <= t.ts < self.start and t.id not in trigger]
        recent = [t for t in mine if self.start <= t.ts <= w.now and t.id not in trigger]
        return View("behavioral_profiler", deep, self.subject, w.now, since, list(self.trigger), history, recent,
                    accounts={self.subject: w.accounts[self.subject]})

    def positions(self) -> dict[str, Finding]:
        """Each specialist's current position: its latest accepted finding."""
        return {f.agent: f for f in self.findings}

    def public_findings(self) -> list[dict]:
        return [f.public() for f in self.findings]

    # --- writes ------------------------------------------------------------------------------------

    def _reject(self, agent: str, action: str, reason: str) -> None:
        self.rejections.append(Rejection(agent, action, reason))
        return None

    def request_rfi(self, role: str, party: str) -> RfiResponse | None:
        if role != "sanctions_checker":
            return self._reject(role, "request_rfi", "only the sanctions checker may send an RFI")
        if party not in self._seen[role]:
            return self._reject(role, "request_rfi", f"{party} is not in the checker's view")
        ans = self.world.rfi_answers.get(party)
        if ans is not None:
            self.rfis[ans.id] = ans
            self._seen[role].add(ans.id)
        return ans

    def submit(self, role: str, draft: Draft, round: int = 0) -> Finding | None:
        if role not in SPECIALISTS:
            return self._reject(role, "submit", "only specialists file findings")
        current = self.positions().get(role)
        if round == 0 and current is not None:
            return self._reject(role, "submit", "already has a position; revise only in a resolution round")
        if round > 0 and current is None:
            return self._reject(role, "submit", "no position to revise")
        if not draft.evidence:
            return self._reject(role, "submit", "a finding must cite evidence")
        missing = [e for e in draft.evidence if not (self.world.exists(e) or e in self.rfis)]
        if missing:
            return self._reject(role, "submit", f"cites IDs that don't exist: {', '.join(missing)}")
        unseen = [e for e in draft.evidence if e not in self._seen[role]]
        if unseen:
            return self._reject(role, "submit", f"cites IDs it was never shown: {', '.join(unseen)}")
        if not (0 <= draft.severity <= 1 and 0 <= draft.confidence <= 1):
            return self._reject(role, "submit", "severity and confidence must be in [0, 1]")
        if current is not None and not set(draft.evidence) - set(current.evidence):
            return self._reject(role, "submit", "a revision must cite evidence the superseded finding didn't")
        f = Finding(
            id=f"F{len(self.findings) + 1:02d}", agent=role, round=round,
            claim=draft.claim, stance=draft.stance, typology=draft.typology,
            severity=round_(draft.severity), confidence=round_(draft.confidence),
            evidence=tuple(dict.fromkeys(draft.evidence)), facts=dict(draft.facts),
            exculpatory=draft.exculpatory, confirmed=draft.confirmed,
            supersedes=current.id if current is not None else None, note=draft.note,
        )
        self.findings.append(f)
        return f

    def log(self, exchange: Exchange) -> None:
        self.exchanges.append(exchange)


def round_(x: float) -> float:
    return round(float(x), 2)
