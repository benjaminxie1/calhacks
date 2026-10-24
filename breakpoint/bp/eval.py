"""Eval against the answer key (R27). The Proof page shows exactly this output.

- Detection: per break type, recall (planted breaks whose expected detection keys
  were all found) and precision (breaks found that belong to a planted break).
- Resolution: final status vs expected, and executed amounts vs the answer key.
- Ledger vs truth: every client (cash + receivable, positions) and firm balance
  equals the correct books built from the true world.
- Invariants: trial balance zero, every entry balanced, no posting into a period
  after it closed, unique idempotency keys, no float columns.
"""
from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import Float, select, text

from bp import calendar, db
from bp.calendar import lock_date
from bp.db import breaks, cases, proposals
from bp.ledger import service as ledger
from bp.money import D, s
from bp.sources.store import SourceStore

TYPES = ["B1", "B2", "B3", "B4", "H2", "N1", "N2"]


def truth_balances(store: SourceStore, upto=None) -> tuple[dict, dict]:
    client, firm = defaultdict(Decimal), defaultdict(Decimal)
    for t in store.truth:
        if upto and t.date > upto:
            continue
        for p in t.postings:
            if p.account.startswith("client:"):
                client[(p.account.split(":")[1], p.asset)] += p.amount
            else:
                firm[(p.account, p.asset)] += p.amount
    return client, firm


def ledger_firm_balances(conn) -> dict:
    rows = conn.execute(text("SELECT account, asset, sum(amount) FROM ledger_lines WHERE account NOT LIKE 'client:%' "
                             "GROUP BY 1, 2"))
    return {(a, s_): v for a, s_, v in rows}


def _nz(d: dict) -> dict:
    return {k: v for k, v in d.items() if v != 0}


def evaluate(conn, store: SourceStore) -> dict:
    brk = [dict(r) for r in conn.execute(select(breaks)).mappings()]
    by_key = {b["detection_key"]: b for b in brk}
    case_status = {r.case_id: r.status for r in conn.execute(select(cases.c.case_id, cases.c.status))}
    executed = [dict(r) for r in conn.execute(select(proposals).where(proposals.c.status == "executed")).mappings()]
    by_case: dict[str, list] = defaultdict(list)
    for p in executed:
        by_case[p["case_id"]].append(p)

    attributed, rows = set(), []
    for ak in store.answer_key:
        keys = ak["expected_detection_keys"]
        found = [k for k in keys if k in by_key]
        attributed |= set(found)
        detected = len(found) == len(keys)
        statuses = set()
        for k in found:
            b = by_key[k]
            statuses.add("auto_cleared" if b["status"] == "auto_cleared" else case_status.get(b["case_id"], "open"))
        final = statuses.pop() if len(statuses) == 1 else ("mixed" if statuses else "undetected")
        amounts_ok, detail = _amounts_ok(ak, [by_key[k] for k in found], by_case)
        rows.append({"break_id": ak["break_id"], "type": ak["type"], "detected": detected, "final_status": final,
                     "expected_status": ak["expected_final_status"], "first_status": ak.get("first_status"),
                     "status_ok": final == ak["expected_final_status"], "amounts_ok": amounts_ok, "detail": detail,
                     "note": ak.get("note", "")})

    flagged = [b for b in brk if b["break_key"] != "ledger-custodian:settlement_timing"]
    false_pos = [b["detection_key"] for b in flagged if b["detection_key"] not in attributed]
    per_type = {}
    for t in TYPES + sorted({r["type"] for r in rows} - set(TYPES)):
        rs = [r for r in rows if r["type"] == t]
        if not rs:
            continue
        per_type[t] = {
            "planted": len(rs), "detected": sum(r["detected"] for r in rs),
            "recall": _ratio(sum(r["detected"] for r in rs), len(rs)),
            "resolved_correctly": sum(r["status_ok"] and r["amounts_ok"] for r in rs),
        }
    n_found = len(flagged)
    precision = _ratio(n_found - len(false_pos), n_found)

    lc = _nz(ledger.client_balances(conn))
    tc, tf = truth_balances(store)
    tc, tf = _nz(tc), _nz(tf)
    lf = _nz(ledger_firm_balances(conn))
    client_diff = {f"{a}:{k}": s(lc.get((a, k), D(0)) - tc.get((a, k), D(0)))
                   for (a, k) in set(lc) | set(tc) if lc.get((a, k), D(0)) != tc.get((a, k), D(0))}
    firm_diff = {f"{a}:{k}": s(lf.get((a, k), D(0)) - tf.get((a, k), D(0)))
                 for (a, k) in set(lf) | set(tf) if lf.get((a, k), D(0)) != tf.get((a, k), D(0))}

    invariants = _invariants(conn)
    timing = [b for b in brk if b["break_key"] == "ledger-custodian:settlement_timing"]
    harmless_auto = sum(1 for b in brk if b["harmless"] and b["status"] == "auto_cleared")
    total_items = len(brk)
    comp = {r.case_id: r.completion for r in conn.execute(select(cases.c.case_id, cases.c.completion))}
    auto_cases = sum(1 for c in comp.values() if c and c.get("playbook"))
    taught = sum(1 for c in comp.values() if c and c.get("teach_session"))
    closed = sum(1 for b in brk if b["status"] in ("resolved", "auto_cleared"))
    return {
        "detection": {"per_type": per_type, "precision": precision,
                      "recall": _ratio(sum(r["detected"] for r in rows), len(rows)), "false_positives": false_pos,
                      "timing_items": len(timing)},
        "resolution": {"rows": rows, "correct": sum(r["status_ok"] and r["amounts_ok"] for r in rows),
                       "total": len(rows)},
        "ledger_vs_truth": {"client_mismatches": client_diff, "firm_mismatches": firm_diff,
                            "equal": not client_diff and not firm_diff},
        "invariants": invariants,
        "auto_resolution_rate": _ratio(auto_cases, len(case_status)),
        "cases": {"total": len(case_status), "auto_resolved": auto_cases, "taught": taught,
                  "open_needs_human": sum(1 for c in case_status.values() if c == "needs_human"),
                  "harmless_auto_cleared": harmless_auto},
        "items": {"total": total_items, "closed": closed},
    }


def _ratio(a, b) -> str:
    return "n/a" if not b else s((D(a) / D(b) * 100).quantize(D("0.1"))) + "%"


def _amounts_ok(ak: dict, found: list[dict], by_case) -> tuple[bool, str]:
    """Executed calculator outputs match the answer key's expected fix amounts."""
    from bp import calculators

    fixes = [f for f in ak["expected_fix"] if f.get("amounts")]
    if not fixes:
        return True, "no amounts expected"
    props = []
    for b in found:
        props += by_case.get(b["case_id"], [])
    outs = {}
    for p in {p["proposal_id"]: p for p in props}.values():
        body = p["body"]
        outs.setdefault(body["action_name"], []).append(calculators.run(body["calculator"], body["calc_inputs"]))
    for f in fixes:
        got = outs.get(f["action"])
        if not got:
            return False, f"no executed {f['action']}"
        a, o = f["amounts"], got[0]
        if f["action"] == "reverse_unfilled":
            ok = (D(o["info"]["unfilled_qty"]) == D(a["qty"]) and abs(D(o["info"]["principal_returned"])) == D(a["principal"])
                  and D(o["info"]["fee_returned"]) == D(a["fee"]))
        elif f["action"] == "fee_refund":
            ok = D(o["info"]["refund"]) == D(a["refund"])
        elif f["action"] == "reverse_duplicate":
            ok = o["info"]["reverses"] == a["txn"]
        elif f["action"] == "post_dividend":
            ok = D(o["info"]["dividend"]) == D(a["amount"])
        elif f["action"] == "split_adjust":
            got_adds = {k: v["new_qty"] - v["old_qty"] for k, v in o["info"].items()}
            ok = got_adds == {k: D(v) for k, v in a.items()}
        elif f["action"] == "reverse_deposit":
            ok = D(o["info"]["reversed"]) == D(a["amount"])
        elif f["action"] == "book_receivable":
            ok = True  # depends on trades between the return and the fix; checked via ledger-vs-truth net
        else:
            ok = True
        if not ok:
            return False, f"{f['action']} amounts differ from the answer key"
    return True, "amounts match the answer key"


def _invariants(conn) -> dict:
    tb = {a: v for a, v in ledger.trial_balance(conn).items() if v != 0}
    unbalanced = conn.execute(text("""SELECT count(*) FROM (SELECT txn_id, asset FROM ledger_lines GROUP BY 1, 2
                                       HAVING sum(amount) <> 0) x""")).scalar()
    late = []
    for r in conn.execute(text("SELECT txn_id, period, effective_date FROM ledger_entries WHERE kind = 'correction'")):
        if r.effective_date >= lock_date(r.period):
            late.append(r.txn_id)
    dup_keys = conn.execute(text("SELECT count(*) - count(DISTINCT idempotency_key) FROM ledger_entries")).scalar()
    floats = [f"{t.name}.{c.name}" for t in db.md.sorted_tables for c in t.columns if isinstance(c.type, Float)]
    checks = {
        "trial_balance_zero": not tb, "all_entries_balanced": unbalanced == 0, "no_closed_period_postings": not late,
        "idempotency_keys_unique": dup_keys == 0, "no_float_columns": not floats,
    }
    return {"checks": checks, "all_green": all(checks.values()), "details": {"trial_balance": {k: s(v) for k, v in tb.items()},
                                                                              "late": late, "floats": floats}}


if __name__ == "__main__":
    from bp.sources.store import get_store

    with db.engine().connect() as c:
        r = evaluate(c, get_store())
    det = r["detection"]
    print(f"detection recall {det['recall']}  precision {det['precision']}  false positives {len(det['false_positives'])}")
    for t, v in det["per_type"].items():
        print(f"  {t}: {v['detected']}/{v['planted']} detected, {v['resolved_correctly']} resolved correctly")
    print(f"resolution {r['resolution']['correct']}/{r['resolution']['total']} correct")
    for row in r["resolution"]["rows"]:
        if not (row["status_ok"] and row["amounts_ok"]):
            print(f"  {row['break_id']}: status {row['final_status']} (expected {row['expected_status']}); {row['detail']}")
    print(f"ledger equals truth: {r['ledger_vs_truth']['equal']}")
    if not r["ledger_vs_truth"]["equal"]:
        print(json.dumps({k: dict(list(v.items())[:10]) for k, v in r["ledger_vs_truth"].items() if k != 'equal'}, indent=1))
    print(f"invariants all green: {r['invariants']['all_green']} {r['invariants']['checks']}")
    print(f"auto-resolution rate {r['auto_resolution_rate']} of cases {r['cases']}")
