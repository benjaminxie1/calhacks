"""Learner (LLM + checks): drafts playbooks. It can never activate one.

Induction (R20): groups resolved historical cases by break key and drafts one
playbook per key with one branch per distinct fix. Teaching (R21): turns a human's
teach session into a new branch (or a new playbook for a new key) whose condition
is built around the fact the human named, plus a test from the case.

Conditions are proposed by the model when one is available, and always checked in
code: the condition must parse, read only context facts, hold for every case it was
learned from, and hold for no case that belongs to another branch. If the model's
proposal fails a check, the deterministic predicate search is used instead.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from itertools import combinations

from pydantic import BaseModel

from bp import config
from bp.agents import llm
from bp.agents.events import emit
from bp.playbooks import conditions
from bp.playbooks import store as pbstore
from bp.playbooks.schema import Branch, Key, Playbook, Provenance
from bp.recon.context import FACTS, from_jsonable

ACTOR = "learner"
TRUST_WORDS = {"lease": "lease", "county": "county", "statement": "billed", "ap": "estimates", "bank": "cleared"}


class LearnError(ValueError):
    pass


def playbook_id_for(key_name: str) -> str:
    return "wf-" + key_name.replace(":", "-").replace("_", "-")


def key_from_name(key_name: str) -> Key:
    srcs, field = key_name.split(":")
    return Key(sources=srcs.split("-"), field=field)


# ------------------------------------------------------------------ predicate search

def _candidates(group: list[dict], trusted: str | None) -> list[tuple[tuple, str]]:
    names = sorted(set.intersection(*[set(f) for f in group]))
    names = [n for n in names if all(f[n] is not None for f in group)]
    bools = [n for n in names if all(isinstance(f[n], bool) for f in group)]
    nums = [n for n in names if all(isinstance(f[n], Decimal) for f in group)]
    ratios = [n for n in nums if n.endswith("_ratio")]
    word = TRUST_WORDS.get(trusted or "", "\0")
    out = []

    def add(prio, expr):
        if all(conditions.evaluate(expr, f) for f in group):
            mentions = word in expr
            out.append(((prio, 0 if mentions else 1, 0 if "*" in expr else 1, expr), expr))

    for b in bools:
        add(0, b if group[0][b] else f"not {b}")
    plain = [n for n in nums if n not in ratios]
    for a, b in combinations(plain, 2):
        add(1, f"{a} == {b}")
        for r in ratios:
            add(1, f"{a} == {b} * {r}")
            add(1, f"{b} == {a} * {r}")
    for n in plain:
        if n.endswith("_bookings"):
            k = group[0][n]
            if k == k.to_integral_value() and 0 <= k <= 5:
                add(2, f"{n} == {int(k)}")
    for a, b in combinations(plain, 2):
        add(3, f"{a} > {b}")
        add(3, f"{a} < {b}")
    out.sort()
    seen, uniq = set(), []
    for k, e in out:
        if e not in seen:
            seen.add(e)
            uniq.append(e)
    return uniq


def search_condition(group: list[dict], others: list[dict], trusted: str | None = None,
                     required: list[str] | None = None, max_terms: int = 3) -> str:
    """Smallest readable conjunction that holds for the whole group and excludes every other case."""
    cands = _candidates(group, trusted)
    chosen = list(required or [])
    for r in chosen:
        if not all(conditions.evaluate(r, f) for f in group):
            raise LearnError(f"the named fact {r!r} does not hold for this case")
    for c in cands:
        if len(chosen) >= max_terms:
            break
        if c in chosen or c.split()[-1] in chosen:
            continue
        if c.startswith("not ") or " > " in c or " < " in c:
            continue  # prefer positive facts and equalities as the readable core
        chosen.append(c)
    def leaks(expr):
        return [o for o in others if conditions.evaluate(expr, o)]
    expr = " and ".join(chosen) if chosen else "True"
    rest = [c for c in cands if c not in chosen]
    while leaks(expr):
        best = max(rest, key=lambda c: sum(not conditions.evaluate(c, o) for o in leaks(expr)), default=None)
        if best is None or all(conditions.evaluate(best, o) for o in leaks(expr)):
            raise LearnError("cannot separate these cases from another branch: conditions overlap")
        chosen.append(best)
        rest.remove(best)
        expr = " and ".join(chosen)
    return expr


class ConditionDraft(BaseModel):
    condition: str
    rationale: str


def _llm_condition(group, others, context, trusted, required, sop, offline_expr) -> tuple[str, str]:
    payload = {"facts_of_cases_this_branch_must_match": group[:8], "facts_of_cases_it_must_not_match": others[:8],
               "available_facts": context, "trusted_source": trusted, "fact_the_human_named": required,
               "sop_excerpt": sop[:3000]}
    task = ("Write the branch condition for this playbook. Use only the available facts, the operators ==, !=, <, >, "
            "and, or, not, and multiplication by a *_ratio fact. It must be true for every case it must match and false "
            "for every case it must not match. Prefer the shortest condition a human would recognise as the cause.")
    ans, be = llm.decide("learner", task, payload, ConditionDraft,
                         lambda: ConditionDraft(condition=offline_expr, rationale="deterministic predicate search"))
    expr = ans.condition
    try:
        if not conditions.names(expr) <= set(context):
            raise LearnError("reads facts outside context")
        if not all(conditions.evaluate(expr, f) for f in group) or any(conditions.evaluate(expr, o) for o in others):
            raise LearnError("does not separate the cases")
        if required and not all(r in expr for r in required):
            raise LearnError("ignores the fact the human named")
        return expr, be
    except (LearnError, conditions.ConditionError):
        return offline_expr, f"{be} (model condition rejected; used predicate search)"


# ------------------------------------------------------------------ induction

def induct(cases: list[dict], sop: str, d: date | None = None, conn=None) -> list[Playbook]:
    by_key: dict[str, list[dict]] = {}
    for c in cases:
        by_key.setdefault(c["break_key"], []).append(c)
    out = []
    for key_name, cs in sorted(by_key.items()):
        groups: dict[tuple, list[dict]] = {}
        for c in cs:
            r = c["resolution"]
            groups.setdefault((r["trust"], tuple(r["actions"])), []).append(c)
        context = FACTS[key_name]
        branches = []
        for (trust, actions), g in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            facts = [from_jsonable(c["facts"]) for c in g]
            others = [from_jsonable(c["facts"]) for k, gg in groups.items() if k != (trust, actions) for c in gg]
            offline_expr = search_condition(facts, others, trust)
            expr, _ = _llm_condition(facts, others, context, trust, None, sop, offline_expr)
            bid = _branch_name(actions)
            first = sorted(g, key=lambda c: c["closed_on"])[0]
            branches.append(Branch(id=bid, when=expr, trust=trust, actions=list(actions), test=first["facts"],
                                   provenance=Provenance(taught_by=first["resolution"]["resolved_by"],
                                                         from_case=first["case_id"])))
        pb = Playbook(id=playbook_id_for(key_name), version=1, status="draft", key=key_from_name(key_name),
                      context=context, branches=branches)
        out.append(pb)
        if conn is not None:
            emit(conn, ACTOR, "playbook_drafted",
                 f"induction: {len(cs)} cases from last season for {key_name} -> {len(branches)} branch(es)",
                 inputs={"playbook": pb.id, "cases": [c["case_id"] for c in cs]},
                 evidence=[{"case": c["case_id"]} for c in cs] + [{"doc": "SOP.md"}], sim_date=d)
    return out


def _branch_name(actions) -> str:
    names = {"share_credit": "occupied-area-share", "fee_credit": "fee-over-limit", "cap_credit": "cap-not-applied",
             "capital_credit": "capital-excluded", "tax_credit": "tax-above-county", "estimates_credit": "estimates-missed",
             "gross_up_credit": "gross-up-not-allowed"}
    for a in actions:
        if a in names:
            return names[a]
    return "-".join(actions)[:30]


def load_history() -> tuple[list[dict], str]:
    cases = json.loads((config.DATA / "history" / "cases_2025.json").read_text())
    sop = (config.DATA / "SOP.md").read_text()
    return cases, sop


# ------------------------------------------------------------------ teaching

def draft_from_teach(conn, *, case: dict, break_facts: list[dict], trusted: str, actions: list[str], fact: str,
                     teacher: str, d: date, history: list[dict] | None = None) -> Playbook:
    key_name = case["break_key"]
    context = FACTS.get(key_name)
    if not context:
        raise LearnError(f"no context step defined for {key_name}")
    if fact not in context:
        raise LearnError(f"{fact!r} is not a fact the context step gathers for {key_name}: {context}")
    group = [from_jsonable(f) for f in break_facts]
    active = pbstore.for_key(conn, key_name, "active")
    others = []
    if active:
        others += [from_jsonable(b.test) for b in active.branches if b.test]
    others += [from_jsonable(c["facts"]) for c in (history or []) if c["break_key"] == key_name]
    offline_expr = search_condition(group, others, trusted, required=[fact], max_terms=2)
    expr, backend = _llm_condition(group, others, context, trusted, [fact], "", offline_expr)
    branch = Branch(id=_branch_name(actions), when=expr, trust=trusted, actions=actions, test=break_facts[0],
                    provenance=Provenance(taught_by=teacher, from_case=case["case_id"]))
    if active:
        if any(b.id == branch.id for b in active.branches):
            branch = branch.model_copy(update={"id": f"{branch.id}-{len(active.branches) + 1}"})
        pb = active.model_copy(update={"version": pbstore.latest_version(conn, active.id) + 1, "status": "draft",
                                       "branches": active.branches + [branch]})
    else:
        pid = playbook_id_for(key_name)
        pb = Playbook(id=pid, version=pbstore.latest_version(conn, pid) + 1, status="draft",
                      key=key_from_name(key_name), context=context, branches=[branch])
    pb = Playbook.model_validate(pb.dump())  # re-run schema checks
    for other in pb.branches:  # the new branch must not overlap any existing branch on its test case
        if other.id != branch.id and other.test and conditions.evaluate(branch.when, from_jsonable(other.test)):
            raise LearnError(f"new branch overlaps {other.id}; tighten the condition")
    pbstore.save(conn, pb, ACTOR, d, note=f"drafted from teach session on {case['case_id']} by {teacher}: "
                                          f"when {expr} trust {trusted} do {actions} [{backend}]")
    return pb
