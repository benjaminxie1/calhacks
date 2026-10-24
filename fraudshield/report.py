"""The evidence synthesizer: an auditable case report built from structured findings only.

It reads Finding.public() projections, the exchange log, rejections and the policy decision.
It never reads an agent's note, and it shows raw records only in the evidence index, for IDs a
finding cited, with memos left out.
"""
from __future__ import annotations

from .case import CaseFile
from .policy import Decision

CLAIMS = {
    "account_takeover_signals": "payment {txn} came from a new device{geo_part} at an unusual hour, to a new payee, "
                                "{amount_vs_baseline_max}x the largest payment in {baseline_days} days",
    "mule_beneficiary": "beneficiary {beneficiary} is {account_age_days} days old, took money from "
                        "{distinct_sources_72h} sources in 72 hours and forwarded {forward_ratio:.0%} of it",
    "established_counterparty": "beneficiary {beneficiary} is {account_age_days} days old with {distinct_payers} "
                                "distinct payers and no onward forwarding",
    "potential_watchlist_match": "payee {party} scores {name_score} against {entry}, listed {listed_on}; "
                                 "secondary identifiers not yet checked",
    "confirmed_watchlist_match": "date of birth {their_dob}{source_part} matches {entry} ({listed_dob})",
    "name_match_discounted": "date of birth {their_dob}{source_part} doesn't match {entry} ({listed_dob}, "
                             "{listed_country})",
    "rfi_reading_unverified": "the RFI reply couldn't be read reliably ({reading_problem}); the match stays "
                              "unconfirmed",
    "blocked_by_ownership": "{chain_text}: blocked parties own {blocked_ownership_pct:g}% of {party}, at or "
                            "above the 50% rule",
    "indirect_exposure_below_threshold": "{chain_text}: blocked parties own {blocked_ownership_pct:g}% of "
                                         "{party}, below the 50% rule, so it isn't blocked",
    "no_watchlist_match": "{parties_screened} parties screened against {entries} entries; no match",
    "established_payee": "payee paid {prior_payments} times before at similar amounts",
    "precedent_payment": "a payment of similar size went to the same payee {precedent_days_before} days earlier ({precedent})",
    "unusual_payment": "new payee, {amount_vs_baseline_max}x the largest payment in {baseline_days} days; "
                       "device and location are the usual ones",
    "recurring_counterparty": "{counterparty} has paid this account {occurrences} times before",
    "structuring_pattern": "{deposits} cash deposits between $9,000 and $10,000 in a week (${total:,}) at "
                           "{branches} branches; {baseline_near_threshold} like that in the baseline",
    "consistent_cash_pattern": "near-threshold cash deposits are normal for this account ({baseline_near_threshold} in the baseline)",
    "pass_through": "${in_7d:,} arrived in 7 days and {ratio:.0%} left within {hours} hours",
    "funds_return_to_origin": "{return_ratio:.1%} of the money returned to origin in {hours} hours via {path_len} "
                              "accounts (common owner: {common_owner}); {cycles_in_window} such cycles in {window_days} days",
    "no_network_pattern": "no mule, cycle or pass-through pattern visible",
    "no_behavioral_anomaly": "activity is consistent with {history_txns} transactions of history",
    "insufficient_history": "only {history_txns} transactions of history; can't build a baseline",
}


def explain(f: dict) -> str:
    facts = dict(f["facts"])
    facts.setdefault("geo_part", "")
    if "path" in facts:
        facts["path_len"] = len(facts["path"]) - 1
    if f["claim"] == "account_takeover_signals" and facts.get("new_geo"):
        facts["geo_part"] = " and a new location"
    facts["chain_text"] = "; ".join(facts.get("chain", []))
    facts["source_part"] = (f" (from the RFI reply, read by {facts['extractor']}, quote verified)"
                            if "extractor" in facts else " (from KYC or registry)")
    try:
        text = CLAIMS[f["claim"]].format(**facts)
    except (KeyError, ValueError):
        text = f["claim"].replace("_", " ")
    if facts.get("instruction_like_text"):
        text += ". The reply contained instructions aimed at the screening system; they were ignored"
    return text


def narrative(case: CaseFile, d: Decision) -> str:
    fs = {f["id"]: f for f in case.public_findings()}
    pos = {f.agent: f.id for f in case.positions().values()}
    line = lambda i: f"{fs[i]['agent'].replace('_', ' ')} ({i}): {explain(fs[i])}"  # noqa: E731
    parts = []
    if d.disposition == "hold_sanctions":
        parts.append("Held. " + "; ".join(line(i) for i in d.cites) + ".")
        if d.overruled:
            parts.append("Overruled under R1: " + "; ".join(line(i) for i in d.overruled) +
                         ". A clean payment history can't clear a confirmed watchlist match.")
    elif d.disposition == "act":
        parts.append("Two or more specialists found independent evidence. " + "; ".join(line(i) for i in d.cites) + ".")
    elif d.disposition == "close":
        parts.append("Closed as a false positive. " + "; ".join(line(i) for i in pos.values()) + ".")
    else:
        parts.append(f"Sent to a human ({d.rule}). " + "; ".join(line(i) for i in d.cites) + ".")
    revised = [e for e in case.exchanges if e.outcome == "revised"]
    if revised:
        parts.append("Resolved in the protocol: " + "; ".join(
            f"{e.agent.replace('_', ' ')} revised to {e.new_finding} after a challenge from {', '.join(e.challenged_by)}"
            for e in revised) + ".")
    return " ".join(parts)


def _evidence_line(case: CaseFile, ref: str) -> str:
    w = case.world
    if ref in w.by_id:
        t = w.by_id[ref]
        extra = " · ".join(x for x in (t.device, t.geo) if x)
        return (f"{t.ts:%Y-%m-%d %H:%M} · {t.src} → {t.dst} · ${t.amount:,.2f} · {t.channel}"
                + (f" · {extra}" if extra else ""))
    if ref in w.accounts:
        a = w.accounts[ref]
        return f"account of {a.party}, opened {a.opened:%Y-%m-%d}, {'internal' if a.internal else 'external'}"
    if ref in w.parties:
        p = w.parties[ref]
        return f"{p.name} ({p.kind}, {p.country})"
    if ref in case.rfis:
        r = case.rfis[ref]
        return f"RFI reply about {r.party} ({r.source}, untrusted text): \"{' '.join(r.text.split())}\""
    for o in w.ownership:
        if o.id == ref:
            return f"{w.parties[o.owner].name} holds {o.pct:g}% of {w.parties[o.owned].name} ({o.source})"
    for e in w.watchlist:
        if e.id == ref:
            return f"{e.name}, born {e.dob}, {e.country}, {e.program}, listed {e.listed_on:%Y-%m-%d}"
    return "?"


def render_markdown(case: CaseFile, d: Decision, scenario: str = "") -> str:
    fs = case.public_findings()
    pos_ids = {f.id for f in case.positions().values()}
    out = [f"# {case.id} · {d.disposition.upper()}", ""]
    if scenario:
        out.append(f"Scenario: `{scenario}` (simulated data). Narrator: template.")
    out += ["", "**Alerts**", ""]
    out += [f"- `{a.id}` {a.rule} on `{a.subject}`: {a.detail}" for a in case.alerts]
    out += ["", f"**Disposition: {d.disposition}** under {d.rule}: {d.rule_text}", "", narrative(case, d), "",
            "## Final positions", "",
            "| Agent | Finding | Stance | Claim | Severity | Confidence | Evidence |", "|---|---|---|---|---|---|---|"]
    for f in fs:
        if f["id"] in pos_ids:
            tag = " (overruled, R1)" if f["id"] in d.overruled else ""
            out.append(f"| {f['agent']} | {f['id']} | {f['stance']}{tag} | {explain(f)} | {f['severity']} | "
                       f"{f['confidence']} | {', '.join(f'`{e}`' for e in f['evidence'][:6])}"
                       f"{' …' if len(f['evidence']) > 6 else ''} |")
    out += ["", "## Findings log (append-only)", "", "| ID | Round | Agent | Stance | Claim | Supersedes |",
            "|---|---|---|---|---|---|"]
    out += [f"| {f['id']} | {f['round']} | {f['agent']} | {f['stance']} | {f['claim']} | {f['supersedes'] or ''} |"
            for f in fs]
    out += ["", "## Disagreement and resolution", ""]
    if not case.exchanges:
        out.append("No conflicts or open items: no resolution round was needed.")
    for e in case.exchanges:
        out.append(f"- Round {e.round}: **{e.agent}** answered {', '.join(e.challenged_by)} → {e.outcome}"
                   f"{f' ({e.new_finding})' if e.new_finding else ''}: {e.reason}")
    if d.unresolved:
        out.append(f"- Still in conflict at the end: {'; '.join(f'{a} vs {b}' for a, b in d.unresolved)}")
    out += ["", "## Recommended actions (proposed; nothing executes; each needs a human analyst)", "",
            "| Action | Target | Why | Cites |", "|---|---|---|---|"]
    out += [f"| {r.action} | `{r.target}` | {r.reason} | {', '.join(r.cites)} |" for r in d.recommendations]
    out += ["", "## Rules the case file enforced", ""]
    out += [f"- Rejected {r.agent}.{r.action}: {r.reason}" for r in case.rejections] or ["- No rejections."]
    cited = sorted({e for f in fs for e in f["evidence"]})
    out += ["", "## Evidence index", ""] + [f"- `{ref}` {_evidence_line(case, ref)}" for ref in cited]
    return "\n".join(out) + "\n"
