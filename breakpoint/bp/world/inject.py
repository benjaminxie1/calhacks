"""Error injector (R4): plants the taxonomy into what the landlords send and writes the
answer key. The true world (what each lease calls for) is never touched; only the
statements the landlords mail, and so what AP books on receipt, change.

Expected amounts are derived from the planting itself (what was added, inflated or
left out) against the true statement, not by re-running the matcher, so the answer
key is an independent check on detection and on every calculator.
"""
from __future__ import annotations

import random

from bp import cam
from bp.models import AnswerKeyRow, Statement, StatementLine
from bp.money import D, cents, s
from bp.world.generate import NO_REPLY, SHOWCASE, World, statement_txn

CAPITAL_ITEMS = ["Roof replacement (Building B)", "Parking lot resurfacing", "Structural repairs to east facade",
                 "Roof replacement", "Parking lot resurfacing and restriping"]
MISSED_MONTH = "2025-11"


def dkey(kind: str, stmt_id: str, line_id: str | None = None) -> str:
    return f"{kind}:{stmt_id}" + (f":{line_id}" if line_id else "")


def plant(stmt: Statement, truth: Statement, lease, kinds: list[str], rng: random.Random, monthly) -> tuple[Statement, dict]:
    """Return the landlord's statement with the planted errors, and per-kind expected credits."""
    st = stmt.model_copy(deep=True)
    lines = st.lines
    exp: dict = {}
    s_pct = lease.share_pct
    n = max(int(l.line_id[1:]) for l in lines)

    def new_line(desc, cat, amt):
        nonlocal n
        n += 1
        lines.append(StatementLine(line_id=f"L{n}", description=desc, category=cat, amount=amt, tenant_amount=D(0)))
        return lines[-1]

    def one(cat):
        return next((l for l in lines if l.category == cat), None)

    def truth_tenant(cat):
        return sum((l.tenant_amount for l in truth.lines if l.category == cat), D(0))

    if "capital" in kinds:
        amt = D(60000) if lease.lease_id == SHOWCASE else D(rng.randint(35, 95) * 1000)
        desc = "Roof replacement" if lease.lease_id == SHOWCASE else CAPITAL_ITEMS[rng.randrange(len(CAPITAL_ITEMS))]
        ln = new_line(desc, "repairs", amt)
        exp["capital"] = {"line_id": ln.line_id, "credit": cam.share_of(amt, s_pct), "amount": amt}
    if "gross_up" in kinds:
        var = cam.variable_base(lines)
        g = (var * (D(95) - st.occupancy_pct) / st.occupancy_pct).to_integral_value()
        new_line("Gross-up of variable expenses to 95% occupancy", "gross_up", g)
        exp["gross_up"] = {"credit": cam.share_of(g, s_pct), "gross_up": g}
    if "tax" in kinds:
        t = one("taxes")
        billed = D(48000) if lease.lease_id == SHOWCASE else (t.amount * D(rng.randint(118, 160)) / D(100)).to_integral_value()
        exp["tax"] = {"credit": cam.share_of(billed, s_pct) - truth_tenant("taxes"), "billed": billed, "county": t.amount}
        t.amount = billed
    if "cap" in kinds:
        adj = one("cap_adjustment")
        assert adj is not None, f"{lease.lease_id}: cap planted where the cap does not bind"
        lines.remove(adj)
        exp["cap"] = {"credit": -truth_tenant("cap_adjustment"), "excess": -adj.amount}
    if "admin_fee" in kinds:
        f = one("admin_fee")
        base = sum((l.amount for l in lines if l.category not in ("admin_fee", "cap_adjustment", "gross_up")), D(0))
        f.amount = cents(base * D(15) / D(100))
        f.description = "Management fee (15% of all of the above)"
        exp["admin_fee"] = {"credit": cam.share_of(f.amount, s_pct) - truth_tenant("admin_fee"), "billed": f.amount}
    sb = s_pct
    if "share" in kinds:
        sb = (lease.premises_sf * D(100) / st.occupied_sf).quantize(D("0.01"))
        st.share_billed_pct = sb
        exp["share"] = {"credit": sum((cam.share_of(l.amount, sb) - cam.share_of(l.amount, s_pct) for l in lines), D(0)),
                        "billed_pct": sb}
    for l in lines:
        l.tenant_amount = cam.share_of(l.amount, sb)
    if "estimates" in kinds:
        st.estimates_credited = st.estimates_credited - monthly
        exp["estimates"] = {"credit": monthly, "missed": MISSED_MONTH}
    if "rounding" in kinds:
        l = lines[rng.randrange(len(lines))]
        delta = D(rng.randint(1, 60)) / D(100) * (1 if rng.randint(0, 1) else -1)
        l.tenant_amount += delta
        exp["rounding"] = {"delta": delta, "line_id": l.line_id}
    st.tenant_total = sum((l.tenant_amount for l in lines), D(0))
    st.balance_due = st.tenant_total - st.estimates_credited
    # every planted dollar is accounted for by exactly one expected credit
    credits = sum((v["credit"] for k, v in exp.items() if k not in ("estimates", "rounding")), D(0))
    assert st.tenant_total - exp.get("rounding", {}).get("delta", D(0)) - truth.tenant_total == credits, lease.lease_id
    return st, exp


def inject(w: World) -> None:
    rng = random.Random(w.seed * 7919 + 1)
    leases = {l.lease_id: l for l in w.leases}
    idx = {s.statement_id: i for i, s in enumerate(w.statements)}
    truth_by_id = {s.statement_id: s for s in w.truth_statements}
    book_idx = {t.source_ref: i for i, t in enumerate(w.booking)}
    truth_idx = {t.source_ref: i for i, t in enumerate(w.truth)}
    n = {"n": 0}

    def bid(kind):
        n["n"] += 1
        return f"BRK-{kind.upper().replace('_', '')}-{n['n']:03d}"

    gross_later = []
    for lid, kinds in sorted(w.plan.items()):
        lease = leases[lid]
        sid = f"STMT-{lid}-2025"
        truth = truth_by_id[sid]
        st, exp = plant(w.statements[idx[sid]], truth, lease, kinds, rng, lease.monthly_estimate_2025)
        w.statements[idx[sid]] = st
        w.booking[book_idx[sid]] = statement_txn(st, w.booking[book_idx[sid]].txn_id)
        if "estimates" in kinds:  # the landlord dropped one payment; AP and the bank have all twelve
            pass
        if "rounding" in kinds:  # within tolerance: the SOP accepts the landlord's figure, so the correct books do too
            t = truth.model_copy(deep=True)
            t.tenant_total += exp["rounding"]["delta"]
            t.balance_due += exp["rounding"]["delta"]
            w.truth[truth_idx[sid]] = statement_txn(t, w.truth[truth_idx[sid]].txn_id)
        on = st.received_on
        base = {"injected_on": on, "root_cause_source": "statement"}
        ll = st.landlord_id
        for kind in kinds:
            e = exp[kind]
            if kind == "rounding":
                w.answer_key.append(AnswerKeyRow(
                    break_id=bid(kind), type="rounding", affected=[{"source": "statement", "id": sid}],
                    expected_detection_keys=[dkey("rounding", sid)], expected_fix=[], expected_final_status="auto_cleared",
                    note=f"{e['line_id']} printed {s(e['delta'])} off the line-by-line share; within $1", **base))
                continue
            if kind == "gross_up" and lid != "LSE-012":
                gross_later.append((lid, sid, e, on))
                continue
            line = e.get("line_id") if kind == "capital" else None
            fix_name = {"share": "share_credit", "capital": "capital_credit", "cap": "cap_credit", "admin_fee": "fee_credit",
                        "tax": "tax_credit", "estimates": "estimates_credit", "gross_up": "gross_up_credit"}[kind]
            fix = [{"action": fix_name, "amounts": {sid: s(e["credit"])}}]
            if kind == "tax":
                fix.append({"action": "request_tax_bills"})
            elif kind == "estimates":
                fix.append({"action": "send_payment_proof"})
            else:
                fix.append({"action": "dispute_notice"})
            final = "needs_human" if kind == "tax" and ll in NO_REPLY else "completed"
            note = {"share": f"billed {s(e.get('billed_pct', 0))}% (occupied area) vs lease {s(lease.share_pct)}%",
                    "capital": f"{e.get('amount') and s(e['amount'])} capital item passed through",
                    "cap": f"cap adjustment of {s(e.get('excess', 0))} left off",
                    "admin_fee": "management fee at 15% of everything vs the lease's fee and base",
                    "tax": f"taxes billed {s(e.get('billed', 0))} vs county {s(e.get('county', 0))}"
                           + ("; landlord never sends the tax bills, so the controller decides" if final == "needs_human" else ""),
                    "estimates": f"{MISSED_MONTH} estimate payment not credited",
                    "gross_up": "grossed up to 95% occupancy; the lease has no gross-up clause — the novel moment, "
                                "taught by the lease admin lead"}[kind]
            w.answer_key.append(AnswerKeyRow(
                break_id=bid(kind), type=kind, affected=[{"source": "statement", "id": sid}, {"source": "lease", "id": lid}],
                expected_detection_keys=[dkey(kind, sid, line)], expected_fix=fix, expected_final_status=final,
                first_status="needs_human" if kind == "gross_up" else None, note=note, **base))
    if gross_later:
        on = gross_later[0][3]
        w.answer_key.append(AnswerKeyRow(
            break_id=bid("gross_up"), type="gross_up", injected_on=on, root_cause_source="statement",
            affected=[{"source": "statement", "id": sid} for _, sid, _, _ in gross_later],
            expected_detection_keys=[dkey("gross_up", sid) for _, sid, _, _ in gross_later],
            expected_fix=[{"action": "gross_up_credit", "amounts": {sid: s(e["credit"]) for _, sid, e, _ in gross_later}},
                          {"action": "dispute_notice"}],
            expected_final_status="completed",
            note=f"{len(gross_later)} stores grossed up by the same landlord — fixed from the March lesson"))
