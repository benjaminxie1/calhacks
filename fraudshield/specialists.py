"""The three specialists. Each reads only its own View and returns Drafts; the case file decides
whether a Draft becomes a Finding.

These are deterministic heuristics so the framework runs offline. A model-backed specialist
implements the same two methods and passes the same case-file checks: it can only cite what it
was shown, and it can only revise by citing something new.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
from statistics import median

from .case import CaseFile, View
from .extract import read_rfi
from .model import Draft, Ownership, Stance, Txn
from .monitor import NAME_HIT
from .names import name_score
from .stream import CASH

S, B, U = Stance.SUSPICIOUS, Stance.BENIGN, Stance.UNKNOWN


def _index(txns: list[Txn]) -> tuple[dict[str, list[Txn]], dict[str, list[Txn]]]:
    out_of: dict[str, list[Txn]] = {}
    into: dict[str, list[Txn]] = {}
    for t in sorted(txns, key=lambda t: t.ts):
        out_of.setdefault(t.src, []).append(t)
        into.setdefault(t.dst, []).append(t)
    return out_of, into


def _pick(candidates: list[Draft | None]) -> Draft | None:
    """Strongest suspicious signal wins; otherwise the best explanation."""
    cs = [c for c in candidates if c is not None]
    sus = [c for c in cs if c.stance is S]
    if sus:
        return max(sus, key=lambda d: d.severity * d.confidence)
    exc = [c for c in cs if c.exculpatory]
    return max(exc, key=lambda d: d.confidence) if exc else None


def _claims(challenges: list[dict]) -> str:
    return ", ".join(f"{c['id']} {c.get('claim', 'confirm')}" for c in challenges)


class Specialist:
    role = ""

    def assess(self, case: CaseFile) -> Draft:
        return self.analyze(case.view(self.role))

    def respond(self, case: CaseFile, challenges: list[dict], rnd: int) -> Draft | str:
        """Answer a challenge: run the one deeper check, revise only with new evidence."""
        current = case.positions()[self.role]
        if case.deep_used(self.role):
            return f"deep check already used; nothing new to answer {_claims(challenges)}"
        draft = self.analyze(case.view(self.role, deep=True))
        if not set(draft.evidence) - set(current.evidence):
            return f"deep check found nothing that changes {current.claim}; challenged by {_claims(challenges)}"
        return draft

    def analyze(self, v: View) -> Draft:
        raise NotImplementedError


class GraphAnalyst(Specialist):
    """Edges only: who paid whom, when, how much. No names, devices or memos."""
    role = "graph_analyst"

    def analyze(self, v: View) -> Draft:
        out_of, into = _index(v.txns)
        candidates = [self._cycle(v, out_of), self._pass_through(v, out_of, into)]
        candidates += [self._counterparty(v, t, out_of, into) for t in v.trigger if t.src == v.subject]
        best = _pick(candidates)
        if best:
            return best
        external = sorted({t.dst for t in v.trigger if t.src == v.subject and not v.accounts[t.dst].internal})
        return Draft("no_network_pattern", B, None, 0.0, 0.5 if external else 0.6,
                     (v.subject, *(t.id for t in v.trigger)), {"external_counterparties": external})

    def _counterparty(self, v: View, t: Txn, out_of, into) -> Draft | None:
        b, acct = t.dst, v.accounts[t.dst]
        if not acct.internal:
            return None
        age = (t.ts - acct.opened).days
        near = [x for x in into.get(b, []) if t.ts - timedelta(hours=72) <= x.ts <= v.now]
        inflow = sum(x.amount for x in near)
        outs = [x for x in out_of.get(b, []) if x.ts >= t.ts - timedelta(hours=72)]
        forward = sum(x.amount for x in outs) / inflow if inflow else 0.0
        sources = {x.src for x in near}
        to_external = any(not v.accounts[x.dst].internal for x in outs)
        score = (0.3 * (age < 30) + (0.3 if len(sources) >= 4 else 0.15 if len(sources) == 3 else 0)
                 + 0.3 * (forward >= 0.8) + 0.1 * to_external)
        facts = {"beneficiary": b, "account_age_days": age, "distinct_sources_72h": len(sources),
                 "forward_ratio": round(forward, 2), "forwards_to_external": to_external}
        if score >= 0.6:
            ev = (b, t.id, *[x.id for x in near if x.id != t.id][:6], *[x.id for x in outs][:6])
            return Draft("mule_beneficiary", S, "mule", min(1.0, 0.25 + score), 0.8, ev, facts)
        window = [x for x in into.get(b, []) if x.ts >= v.since]
        payers = {x.src for x in window}
        if age >= 365 and len(payers) >= 10 and forward < 0.5:
            ev = (b, t.id, *[x.id for x in window[-3:]])
            return Draft("established_counterparty", B, None, 0.0, 0.75, ev,
                         {**facts, "distinct_payers": len(payers)}, exculpatory=True)
        return None

    def _pass_through(self, v: View, out_of, into) -> Draft | None:
        ins = [t for t in into.get(v.subject, []) if t.ts >= v.now - timedelta(days=7)]
        total_in = sum(t.amount for t in ins)
        if total_in < 20_000:
            return None
        first = min(t.ts for t in ins)
        outs = [t for t in out_of.get(v.subject, []) if t.ts >= first]
        ratio = sum(t.amount for t in outs) / total_in
        if not 0.8 <= ratio <= 1.2:  # pass-through means roughly what came in went out
            return None
        facts = {"in_7d": round(total_in), "cash_in": round(sum(t.amount for t in ins if t.src == CASH)),
                 "out_after": round(sum(t.amount for t in outs)), "ratio": round(ratio, 2),
                 "hours": round((max(t.ts for t in outs) - first).total_seconds() / 3600)}
        return Draft("pass_through", S, "pass_through", 0.7, 0.75,
                     (v.subject, *(t.id for t in ins), *(t.id for t in outs)), facts)

    def _cycle(self, v: View, out_of) -> Draft | None:
        cycles = []
        for s in (t for t in v.txns if t.src == v.subject and t.amount >= 25_000):
            path = _return_path(out_of, s.dst, v.subject, s.ts, s.ts + timedelta(days=7), 0.9 * s.amount, 3)
            if path:
                cycles.append([s, *path])
        trig = {t.id for t in v.trigger}
        current = [c for c in cycles if any(x.id in trig for x in c)]
        if not current:
            return None
        c = current[-1]
        path = [c[0].src, *(x.dst for x in c)]
        owners = {v.owners.get(v.accounts[a].party, v.accounts[a].party) for a in path}
        older = [x for x in cycles if x is not c][:2]
        facts = {"path": path, "hours": round((c[-1].ts - c[0].ts).total_seconds() / 3600),
                 "return_ratio": round(c[-1].amount / c[0].amount, 3), "common_owner": len(owners) == 1,
                 "cycles_in_window": len(cycles), "window_days": (v.now - v.since).days}
        return Draft("funds_return_to_origin", S, "round_trip", 0.6, 0.7,
                     (*(x.id for x in c), *(x.id for cyc in older for x in cyc)), facts)


def _blocked_share(entity: str, ownership: list[Ownership], blocked: set[str], depth: int = 0) -> tuple[float, list[Ownership]]:
    """Aggregate stake held in `entity` by blocked parties. An owner that is itself 50%+ owned by blocked
    parties is blocked and counts in full. Stakes are summed, never multiplied down the chain."""
    pct, links = 0.0, []
    for o in ownership:
        if o.owned != entity:
            continue
        if o.owner in blocked:
            pct, links = pct + o.pct, [*links, o]
        elif depth < 3:
            sub, chain = _blocked_share(o.owner, ownership, blocked, depth + 1)
            if sub >= BLOCKING_PCT:
                pct, links = pct + o.pct, [*links, o, *chain]
    return pct, links


def _return_path(out_of, node, target, after, before, floor, hops) -> list[Txn] | None:
    if hops == 0:
        return None
    for x in out_of.get(node, []):
        if after < x.ts <= before and x.amount >= floor:
            if x.dst == target:
                return [x]
            rest = _return_path(out_of, x.dst, target, x.ts, before, floor, hops - 1)
            if rest:
                return [x, *rest]
    return None


BLOCKING_PCT = 50.0


class SanctionsChecker(Specialist):
    """Names and countries of the parties on the alert and their registry owners, against the watchlist.
    Applies the 50% ownership rule. Can send one RFI, whose free-text reply goes through extract.read_rfi."""
    role = "sanctions_checker"

    def __init__(self, extractor: str = "regex"):
        self.extractor = extractor

    def analyze(self, v: View) -> Draft:
        hits = {}
        for p in v.parties.values():
            for e in v.watchlist:
                score = max(name_score(p.name, n) for n in (e.name, *e.aliases))
                if score >= NAME_HIT and score > hits.get(p.id, (0, None))[0]:
                    hits[p.id] = (score, e)
        own_ids = tuple(o.id for o in v.ownership)
        if not hits:
            return Draft("no_watchlist_match", B, None, 0.0, 0.9, (*sorted(v.parties), *own_ids),
                         {"parties_screened": len(v.parties), "entries": len(v.watchlist),
                          "registry_links": len(v.ownership)})
        direct = {acct.party for acct in v.accounts.values()}
        confirmed = {pid for pid, (_, e) in hits.items() if v.parties[pid].dob == e.dob}
        for pid in sorted(direct & set(hits)):
            if v.parties[pid].dob:  # KYC or registry already holds the date of birth
                return self._compare(self._facts(v, pid, *hits[pid]), self._cites(v, pid, hits[pid][1]),
                                     v.parties[pid].dob, v.parties[pid].country, hits[pid][1])
        for pid in sorted(direct):
            pct, links = _blocked_share(pid, v.ownership, confirmed)
            if pct > 0:
                listed = next(o.owner for o in links if o.owner in confirmed)
                entry = hits[listed][1]
                facts = {**self._facts(v, listed, *hits[listed]), "party": pid, "listed_person": listed,
                         "blocked_ownership_pct": pct,
                         "chain": [f"{v.parties[o.owner].name} holds {o.pct:g}% of {v.parties[o.owned].name}"
                                   for o in links]}
                ev = (pid, listed, entry.id, *(o.id for o in links), *self._txns(v, pid))
                if pct >= BLOCKING_PCT:
                    return Draft("blocked_by_ownership", S, "sanctions", 1.0, 0.95, ev, facts, confirmed=True)
                return Draft("indirect_exposure_below_threshold", S, "sanctions_exposure", 0.6, 0.8, ev, facts)
        pid = max((p for p in hits if not v.parties[p].dob), key=lambda p: hits[p][0], default=None)
        if pid is None:
            pid = max(hits, key=lambda p: hits[p][0])
            return self._compare(self._facts(v, pid, *hits[pid]), self._cites(v, pid, hits[pid][1]),
                                 v.parties[pid].dob, v.parties[pid].country, hits[pid][1])
        score, e = hits[pid]
        return Draft("potential_watchlist_match", S, "sanctions", 0.95, 0.55 + 0.1 * (v.parties[pid].country == e.country),
                     self._cites(v, pid, e), self._facts(v, pid, score, e), confirmed=False)

    def respond(self, case: CaseFile, challenges: list[dict], rnd: int) -> Draft | str:
        current = case.positions()[self.role]
        if current.confirmed is not False:
            return f"{current.claim} stands; nothing to confirm (challenged by {_claims(challenges)})"
        if case.deep_used(self.role):
            return "RFI already sent; match stays unconfirmed"
        v = case.view(self.role, deep=True)
        ans = case.request_rfi(self.role, current.facts["party"])
        if ans is None:
            return "RFI unanswered; match stays unconfirmed"
        reading = read_rfi(ans.text, self.extractor)
        facts = {**current.facts, **reading.facts()}
        evidence = (*current.evidence, ans.id)
        if not reading.verified:  # fail closed: an unverified reading neither confirms nor discounts
            return Draft("rfi_reading_unverified", S, "sanctions", 0.95, current.confidence, evidence, facts,
                         confirmed=False)
        entry = next(e for e in v.watchlist if e.id == current.facts["entry"])
        return self._compare(facts, evidence, reading.dob, reading.country, entry)

    def _txns(self, v: View, pid: str) -> list[str]:
        accts = {a for a, acct in v.accounts.items() if acct.party == pid}
        return [t.id for t in v.trigger if t.src in accts or t.dst in accts]

    def _cites(self, v: View, pid: str, e) -> tuple[str, ...]:
        return (pid, e.id, *self._txns(v, pid))

    def _facts(self, v: View, pid: str, score: float, e) -> dict:
        return {"party": pid, "entry": e.id, "name_score": round(score, 2), "payment_country": v.parties[pid].country,
                "listed_country": e.country, "listed_on": e.listed_on.date().isoformat(), "program": e.program}

    def _compare(self, facts, evidence, dob, country, entry) -> Draft:
        facts = {**facts, "their_dob": dob, "listed_dob": entry.dob, "their_country": country,
                 "dob_match": dob == entry.dob, "country_match": country == entry.country}
        if dob == entry.dob:
            return Draft("confirmed_watchlist_match", S, "sanctions", 1.0, 0.97, evidence, facts, confirmed=True)
        return Draft("name_match_discounted", B, None, 0.0, 0.9, evidence, facts, exculpatory=True)


class BehavioralProfiler(Specialist):
    """The subject account's own history: devices, places, hours, payees, amounts."""
    role = "behavioral_profiler"

    def analyze(self, v: View) -> Draft:
        hist = v.txns
        if len(hist) < 10:
            return Draft("insufficient_history", U, None, 0.0, 0.3, (v.subject,), {"history_txns": len(hist)})
        out = [t for t in hist if t.src == v.subject]
        initiated = [t for t in out if t.device]
        candidates = [self._outbound(v, t, out, initiated) for t in v.trigger if t.src == v.subject]
        candidates += [self._recurring(v, t, hist) for t in v.trigger if t.dst == v.subject and t.src != CASH]
        candidates.append(self._structuring(v, hist))
        best = _pick(candidates)
        if best:
            return best
        return Draft("no_behavioral_anomaly", B, None, 0.0, 0.6, (v.subject, *(t.id for t in v.trigger)),
                     {"history_txns": len(hist), "baseline_days": (v.trigger[0].ts - v.since).days})

    def _outbound(self, v: View, t: Txn, out: list[Txn], initiated: list[Txn]) -> Draft | None:
        devices = {x.device for x in initiated}
        geos = {x.geo for x in initiated if x.geo}
        hours = [x.ts.hour for x in initiated] or [t.ts.hour]
        max_out = max((x.amount for x in out), default=0.0)
        prior = [x for x in out if x.dst == t.dst]
        similar = [x for x in prior if abs(x.amount - t.amount) <= 0.25 * t.amount]
        ratio = t.amount / max_out if max_out else float("inf")
        new_device = t.device is not None and t.device not in devices
        new_geo = t.geo is not None and t.geo not in geos
        odd_hour = not (min(hours) <= t.ts.hour <= max(hours))
        facts = {"txn": t.id, "amount": round(t.amount), "amount_vs_baseline_max": round(ratio, 1),
                 "new_payee": not prior, "new_device": new_device, "new_geo": new_geo, "odd_hour": odd_hour,
                 "usual_hours": f"{min(hours)}:00-{max(hours)}:59", "baseline_days": (t.ts - v.since).days}
        sample = tuple(x.id for x in initiated[-3:])
        score = 0.3 * new_device + 0.2 * new_geo + 0.15 * (not prior) + 0.15 * odd_hour + 0.2 * (ratio > 3)
        if new_device and score >= 0.6:
            return Draft("account_takeover_signals", S, "account_takeover", 0.85, min(0.95, 0.5 + score / 2),
                         (v.subject, t.id, *sample), facts)
        if len(prior) >= 3 and abs(t.amount - median(x.amount for x in prior)) <= 0.25 * t.amount:
            return Draft("established_payee", B, None, 0.0, 0.8, (t.id, *(x.id for x in prior[-12:])),
                         {**facts, "prior_payments": len(prior)}, exculpatory=True)
        if similar:
            p = similar[-1]
            return Draft("precedent_payment", B, None, 0.0, 0.75, (t.id, *(x.id for x in similar)),
                         {**facts, "precedent": p.id, "precedent_days_before": (t.ts - p.ts).days},
                         exculpatory=True)
        if not prior and ratio > 3:
            return Draft("unusual_payment", S, "unusual_payment", 0.6, 0.7, (v.subject, t.id, *sample), facts)
        return None

    def _recurring(self, v: View, t: Txn, hist: list[Txn]) -> Draft | None:
        prior = [x for x in hist if x.src == t.src]
        if len(prior) < 2:
            return None
        return Draft("recurring_counterparty", B, None, 0.0, 0.7, (t.id, *(x.id for x in prior[-12:])),
                     {"counterparty": t.src, "occurrences": len(prior)}, exculpatory=True)

    def _structuring(self, v: View, hist: list[Txn]) -> Draft | None:
        lo, hi = 9_000, 10_000
        week = v.now - timedelta(days=7)
        near = [t for t in (*v.trigger, *v.recent)
                if t.src == CASH and t.dst == v.subject and lo <= t.amount < hi and t.ts >= week]
        if len(near) < 3:
            return None
        before = sum(1 for t in hist if t.src == CASH and lo <= t.amount < hi)
        facts = {"deposits": len(near), "total": round(sum(t.amount for t in near)),
                 "branches": len({t.geo for t in near}), "baseline_near_threshold": before,
                 "largest_baseline_cash": round(max((t.amount for t in hist if t.src == CASH), default=0))}
        if before >= 6:
            return Draft("consistent_cash_pattern", B, None, 0.0, 0.7, (v.subject, *(t.id for t in near)),
                         facts, exculpatory=True)
        return Draft("structuring_pattern", S, "structuring", 0.8, 0.85, (v.subject, *(t.id for t in near)), facts)


def team(extractor: str = "regex") -> tuple[Specialist, ...]:
    return GraphAnalyst(), SanctionsChecker(extractor), BehavioralProfiler()


DEFAULT = team()
