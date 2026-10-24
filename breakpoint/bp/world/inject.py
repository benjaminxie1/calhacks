"""Break injector (R4): plants the taxonomy into the derived views and writes the
answer key. The true world is never touched; only what systems report changes."""
from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

from bp.calendar import END, next_bd
from bp.models import AnswerKeyRow, Execution, LedgerTxn, Posting
from bp.money import D, cents, s
from bp.world.generate import FEES, World, acct_key, compute_fee, fee_schedule_on

NO_REPLY = {"ACC-0052"}  # mirrors bp.agents.outreach.NO_REPLY
COUNTS = {"B1": 6, "B2": 6, "B3": 4, "B4": 3, "H2": 10}


def _spread(rng: random.Random, items: list, n: int) -> list:
    """Pick n items spread across the (date-sorted) list, deterministically."""
    if len(items) < n:
        raise RuntimeError(f"need {n} candidates, have {len(items)}")
    step = len(items) / n
    out = []
    for i in range(n):
        lo, hi = int(i * step), max(int(i * step), int((i + 1) * step) - 1)
        out.append(items[rng.randint(lo, hi)])
    return out


def inject(w: World) -> None:
    rng = random.Random(w.seed * 7919 + 1)
    tier = {a.account_id: a.tier for a in w.accounts}
    orders = {o.order_id: o for o in w.orders}
    fills: dict[str, list[Execution]] = {}
    for e in w.executions:
        fills.setdefault(e.order_id, []).append(e)
    idx = {(t.kind, t.source_ref): i for i, t in enumerate(w.booking)}
    used_orders: set[str] = set()
    risky_accts = {t.account_id for t in w.bank_transfers if t.status == "returned"}
    n = {"n": 0}

    def bid(kind):
        n["n"] += 1
        return f"BRK-{kind}-{n['n']:03d}"

    def trade_ok(o, last_ok=END):
        return (o.order_id not in used_orders and o.account_id not in risky_accts
                and next_bd(o.trade_date) <= last_ok)

    def replace(i, **changes):
        t = w.booking[i]
        w.booking[i] = LedgerTxn(**{**t.model_dump(), **changes})

    # B1: partial fill booked as full ------------------------------------------------
    cands = [o for o in w.orders if o.status == "partially_filled" and o.side == "buy" and trade_ok(o)]
    for o in _spread(rng, cands, COUNTS["B1"]):
        used_orders.add(o.order_id)
        fs = [e for e in fills[o.order_id] if e.exec_type == "new"]
        exec_qty = sum(e.fill_qty for e in fs)
        true_gross = sum(cents(e.fill_qty * e.fill_px) for e in fs)
        extra = o.qty - exec_qty
        last_px = fs[-1].fill_px
        gross = true_gross + cents(extra * last_px)
        sched = fee_schedule_on(o.trade_date)
        fee, true_fee = compute_fee(tier[o.account_id], o.qty, sched), compute_fee(tier[o.account_id], exec_qty, sched)
        a = acct_key(o.account_id)
        replace(idx[("trade", o.order_id)], postings=[
            Posting(account=a, asset="USD", amount=-gross), Posting(account="street", asset="USD", amount=gross),
            Posting(account=a, asset=o.symbol, amount=o.qty), Posting(account="street", asset=o.symbol, amount=-o.qty),
            Posting(account=a, asset="USD", amount=-fee), Posting(account="revenue:fees", asset="USD", amount=fee),
        ], reason=f"buy {o.qty} {o.symbol}")
        w.answer_key.append(AnswerKeyRow(
            break_id=bid("B1"), type="B1", injected_on=o.trade_date,
            affected=[{"source": "ledger", "id": o.order_id}, {"source": "executions", "id": o.order_id}],
            expected_detection_keys=[f"trade_qty:{o.order_id}"], root_cause_source="ledger",
            expected_fix=[{"action": "reverse_unfilled", "amounts": {
                "qty": s(extra), "principal": s(cents(extra * last_px)), "fee": s(fee - true_fee)}},
                {"action": "notify_client_service"}],
            expected_final_status="completed", note=f"ledger {o.qty} vs executions {exec_qty}"))

    # B2: commission at the wrong (pre-change) schedule -------------------------------
    v1, v2 = FEES[0], FEES[1]
    cands = []
    for o in w.orders:
        if o.status != "filled" or o.trade_date < v2.effective_from or not trade_ok(o):
            continue
        if compute_fee(tier[o.account_id], o.qty, v1) != compute_fee(tier[o.account_id], o.qty, v2):
            cands.append(o)
    for o in _spread(rng, cands, COUNTS["B2"]):
        used_orders.add(o.order_id)
        old, new = compute_fee(tier[o.account_id], o.qty, v1), compute_fee(tier[o.account_id], o.qty, v2)
        i = idx[("trade", o.order_id)]
        ps = [p.model_copy() for p in w.booking[i].postings]
        ps[4] = Posting(account=ps[4].account, asset="USD", amount=-old)
        ps[5] = Posting(account="revenue:fees", asset="USD", amount=old)
        replace(i, postings=ps)
        w.answer_key.append(AnswerKeyRow(
            break_id=bid("B2"), type="B2", injected_on=o.trade_date,
            affected=[{"source": "ledger", "id": o.order_id}, {"source": "fee_schedule", "id": f"v{v2.version}"}],
            expected_detection_keys=[f"fee:{o.order_id}"], root_cause_source="ledger",
            expected_fix=[{"action": "fee_refund", "amounts": {"refund": s(old - new)}}, {"action": "notify_client"}],
            expected_final_status="completed", note=f"charged v1 {old}, schedule v2 {new}"))

    # B3: duplicate execution report booked twice --------------------------------------
    cands = [o for o in w.orders if o.status == "filled" and trade_ok(o) and len(fills[o.order_id]) == 1]
    for o in _spread(rng, cands, COUNTS["B3"]):
        used_orders.add(o.order_id)
        e = fills[o.order_id][0]
        dup = e.model_copy(update={"poss_dup": True})
        w.executions.insert(w.executions.index(e) + 1, dup)
        i = idx[("trade", o.order_id)]
        t = w.booking[i]
        w.booking.insert(i + 1, t.model_copy(update={"txn_id": t.txn_id + "-R"}))
        idx = {(x.kind, x.source_ref) if not x.txn_id.endswith("-R") else ("dup", x.txn_id): j
               for j, x in enumerate(w.booking)}
        principal = -t.postings[0].amount
        w.answer_key.append(AnswerKeyRow(
            break_id=bid("B3"), type="B3", injected_on=o.trade_date,
            affected=[{"source": "ledger", "id": t.txn_id + "-R"}, {"source": "executions", "id": e.exec_id}],
            expected_detection_keys=[f"trade_qty:{o.order_id}"], root_cause_source="ledger",
            expected_fix=[{"action": "request_info_broker"},
                          {"action": "reverse_duplicate", "amounts": {
                              "txn": t.txn_id + "-R", "qty": s(o.qty), "principal": s(principal),
                              "fee": s(t.postings[5].amount)}}],
            expected_final_status="completed", note="broker re-sent the report (PossDup); ledger booked it twice"))

    # B4: dividend not credited -----------------------------------------------------------
    divs = [i for i, t in enumerate(w.booking) if t.kind == "dividend" and t.postings[0].account.split(":")[1]
            not in risky_accts]
    picks = sorted(_spread(rng, divs, COUNTS["B4"]), reverse=True)
    for i in picks:
        t = w.booking.pop(i)
        acct = t.postings[0].account.split(":")[1]
        ca_id = t.source_ref.split(":")[0]
        w.answer_key.append(AnswerKeyRow(
            break_id=bid("B4"), type="B4", injected_on=t.date,
            affected=[{"source": "custodian", "id": t.source_ref}, {"source": "corporate_actions", "id": ca_id}],
            expected_detection_keys=[f"cash:{acct}:{t.date.isoformat()}"], root_cause_source="ledger",
            expected_fix=[{"action": "post_dividend", "amounts": {"amount": s(t.postings[0].amount)}}],
            expected_final_status="completed", note=t.reason))
    idx = {(t.kind, t.source_ref): j for j, t in enumerate(w.booking)}

    # H2: custodian reports principal rounded differently by a cent -----------------------
    settled = [st for st in w.settlements if st.settle_date <= END and st.trade_ref not in used_orders
               and orders[st.trade_ref].account_id not in risky_accts]
    for st in _spread(rng, settled, COUNTS["H2"]):
        used_orders.add(st.trade_ref)
        j = w.settlements.index(st)
        w.settlements[j] = st.model_copy(update={"net_amount": st.net_amount + D("0.01") * (1 if rng.randint(0, 1) else -1)})
        w.answer_key.append(AnswerKeyRow(
            break_id=bid("H2"), type="H2", injected_on=st.settle_date,
            affected=[{"source": "custodian", "id": st.trade_ref}, {"source": "ledger", "id": st.trade_ref}],
            expected_detection_keys=[f"price:{st.trade_ref}"], root_cause_source="custodian",
            expected_fix=[], expected_final_status="auto_cleared", note="rounding difference of $0.01"))

    # N1: stock split not applied in the ledger (both splits) ------------------------------
    for ca in [c for c in w.corporate_actions if c.type == "split"]:
        rows = [t for t in w.booking if t.kind == "split" and t.source_ref.startswith(ca.action_id + ":")]
        w.booking = [t for t in w.booking if t not in rows]
        aug = ca.ex_date.month == 8
        w.answer_key.append(AnswerKeyRow(
            break_id=bid("N1"), type="N1", injected_on=ca.ex_date,
            affected=[{"source": "custodian", "id": t.source_ref} for t in rows],
            expected_detection_keys=[f"position:{t.postings[0].account.split(':')[1]}:{ca.symbol}:{ca.ex_date}"
                                     for t in rows],
            root_cause_source="ledger",
            expected_fix=[{"action": "split_adjust", "amounts": {
                t.postings[0].account.split(":")[1]: s(t.postings[0].amount) for t in rows}}],
            expected_final_status="completed", first_status="needs_human" if aug else None,
            note=f"{ca.symbol} {ca.ratio}:1 split, {len(rows)} holders"
                 + (" — the novel moment; taught by the ops lead" if aug else " — fixed from the August lesson")))

    # N2: ACH deposit returned after the client bought shares --------------------------------
    for tr in [t for t in w.bank_transfers if t.status == "returned"]:
        rows = [t for t in w.booking if t.source_ref in (tr.transfer_id, f"{tr.transfer_id}:receivable")
                and t.kind in ("ach_return", "receivable")]
        w.booking = [t for t in w.booking if t not in rows]
        deficit = next((t.postings[0].amount for t in rows if t.kind == "receivable"), D(0))
        w.answer_key.append(AnswerKeyRow(
            break_id=bid("N2"), type="N2", injected_on=tr.status_date,
            affected=[{"source": "bank", "id": tr.transfer_id}, {"source": "ledger", "id": tr.transfer_id}],
            expected_detection_keys=[f"bank:{tr.transfer_id}"], root_cause_source="bank",
            expected_fix=[{"action": "reverse_deposit", "amounts": {"amount": s(tr.amount)}},
                          {"action": "book_receivable", "amounts": {"amount": s(deficit)}},
                          {"action": "request_funds"}],
            expected_final_status="needs_human" if tr.account_id in NO_REPLY else "completed",
            first_status="needs_human",
            note=f"R01 return of {tr.amount} after shares were bought"
                 + ("; client never answers the cash call, so the controller decides on liquidation"
                    if tr.account_id in NO_REPLY else "")))
