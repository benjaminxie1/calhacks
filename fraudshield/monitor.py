"""Transaction monitoring: the rules that raise alerts. The swarm sits behind this, as analysts do."""
from __future__ import annotations

from datetime import datetime, timedelta

from .model import Alert, Txn
from .names import name_score
from .stream import CASH, World

LARGE = 10_000
NEAR_THRESHOLD = (9_000, 10_000)
NAME_HIT = 0.85
ROUND_TRIP_MIN = 25_000
LOOKBACK = timedelta(days=90)
OUTBOUND = ("wire", "ach", "p2p")


def watchlist_hit(world: World, name: str, at: datetime) -> tuple[float, str] | None:
    best = max(((name_score(name, n), e.id) for e in world.watchlist if e.listed_on <= at
                for n in (e.name, *e.aliases)), default=None)
    return best if best and best[0] >= NAME_HIT else None


def scan(world: World) -> list[Alert]:
    day = datetime(world.now.year, world.now.month, world.now.day)
    today = [t for t in world.txns if t.ts >= day]
    alerts: list[tuple[str, str, tuple[str, ...], datetime, str]] = []
    for t in today:
        if not world.accounts[t.src].internal or t.channel not in OUTBOUND:
            continue
        if t.amount >= LARGE and not any(p.src == t.src and p.dst == t.dst and t.ts - LOOKBACK <= p.ts < t.ts
                                         for p in world.touching(t.src)):
            alerts.append(("LARGE_NEW_PAYEE", t.src, (t.id,), t.ts,
                           f"${t.amount:,.0f} {t.channel} to a payee not paid in the last 90 days"))
        party = world.parties[world.accounts[t.dst].party]
        hit = watchlist_hit(world, party.name, t.ts)
        if hit:
            alerts.append(("WATCHLIST_NAME", t.src, (t.id,), t.ts,
                           f"payee name scores {hit[0]:.2f} against watchlist entry {hit[1]}"))
    for acct in {t.dst for t in world.touching(CASH) if t.ts >= day - timedelta(days=6)}:
        near = [t for t in world.touching(acct) if t.src == CASH and t.ts >= day - timedelta(days=6)
                and NEAR_THRESHOLD[0] <= t.amount < NEAR_THRESHOLD[1]]
        if len(near) >= 3:
            alerts.append(("CASH_NEAR_THRESHOLD", acct, tuple(t.id for t in near), near[-1].ts,
                           f"{len(near)} cash deposits between $9,000 and $10,000 in 7 days"))
    for t in today:
        if not world.accounts[t.dst].internal or t.amount < ROUND_TRIP_MIN:
            continue
        for s in world.touching(t.dst):
            if s.src == t.dst and t.ts - timedelta(days=7) <= s.ts < t.ts and s.amount >= ROUND_TRIP_MIN:
                path = _path(world, s.dst, t.src, s.ts, t.ts, 0.9 * s.amount, hops=2)
                if path is not None:
                    legs = (s, *path, t)
                    alerts.append(("ROUND_TRIP", t.dst, tuple(x.id for x in legs), t.ts,
                                   f"{t.amount / s.amount:.0%} of ${s.amount:,.0f} returned to origin in "
                                   f"{(t.ts - s.ts).total_seconds() / 3600:.0f} hours"))
                    break
    return [Alert(f"alert:AL-{i + 1:02d}", *a) for i, a in enumerate(sorted(alerts, key=lambda a: a[3]))]


def _path(world: World, node: str, target: str, after: datetime, before: datetime,
          floor: float, hops: int) -> list[Txn] | None:
    if node == target:
        return []
    if hops == 0:
        return None
    for x in world.touching(node):
        if x.src == node and after < x.ts < before and x.amount >= floor:
            rest = _path(world, x.dst, target, x.ts, before, floor, hops - 1)
            if rest is not None:
                return [x, *rest]
    return None


def cases(world: World) -> list[list[Alert]]:
    """Alerts on the same subject are worked as one case."""
    grouped: dict[str, list[Alert]] = {}
    for a in scan(world):
        grouped.setdefault(a.subject, []).append(a)
    return list(grouped.values())
