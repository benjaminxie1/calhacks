"""Deterministic matcher (R12-R14). Creates breaks; does nothing else.

Three recon levels:
  transaction  ledger vs custodian settlements (qty, principal), ledger fee vs fee schedule,
               ledger vs bank transfers, plus the T+1 timing item for unsettled trades
  position     ledger (settled basis) vs custodian end-of-day positions
  cash         ledger client cash + receivable (settled basis) vs custodian cash

Every break records `deltas`: how far the ledger sits from the trusted view per
(account, asset), effective from a settle date. Position and cash recon subtract the
deltas of open breaks, so one root cause produces one break, not three.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from bp.agents.events import emit, jsonable
from bp.calendar import next_bd
from bp.db import breaks
from bp.ledger import service as ledger
from bp.money import D, cents, s
from bp.sources import Sources
from bp.world.generate import compute_fee

ACTOR = "matcher"
K_TRADE_QTY = "ledger-custodian:trade_qty"
K_PRICE = "ledger-custodian:principal"
K_FEE = "ledger-fee_schedule:commission"
K_BANK = "ledger-bank:cash"
K_POS = "ledger-custodian:position_qty"
K_CASH = "ledger-custodian:cash"
K_TIMING = "ledger-custodian:settlement_timing"
H2_TOLERANCE = D("0.01")


def summarize_trade_entries(ents: list[dict], account_id: str, symbol: str) -> dict:
    """Ledger view of one order from all entries carrying its source_ref (bookings and corrections)."""
    acct = f"client:{account_id}"
    qty = principal = fee = D(0)
    bookings = 0
    for e in ents:
        if e["kind"] == "trade":
            bookings += 1
        e_fee = sum((p["amount"] for p in e["postings"] if p["account"] == "revenue:fees"), D(0))
        cash = sum((p["amount"] for p in e["postings"] if p["account"] == acct and p["asset"] == "USD"), D(0))
        qty += sum((p["amount"] for p in e["postings"] if p["account"] == acct and p["asset"] == symbol), D(0))
        fee += e_fee
        principal += cash + e_fee
    return {"qty": qty, "principal": principal, "fee": fee, "cash": principal - fee, "bookings": bookings}


class Matcher:
    def __init__(self, sources: Sources):
        self.src = sources

    # ------------------------------------------------------------------ helpers
    def _next_id(self, conn) -> str:
        n = conn.execute(text("SELECT count(*) FROM breaks")).scalar() + 1
        return f"BRK-{n:05d}"

    def _create(self, conn, d: date, *, detection_key, break_key, level, account_id=None, symbol=None, ref=None,
                sources, values, deltas=None, amount_usd=D(0), harmless=False, status="open", reason=None) -> bool:
        res = conn.execute(insert(breaks).values(
            break_id=self._next_id(conn), detection_key=detection_key, break_key=break_key, level=level,
            account_id=account_id, symbol=symbol, ref=ref, sources=sources, vals=jsonable(values),
            deltas=jsonable(deltas or []), amount_usd=abs(amount_usd), detected_on=d, status=status,
            harmless=harmless, reason=reason, resolved_on=d if status == "auto_cleared" else None,
        ).on_conflict_do_nothing(index_elements=["detection_key"]).returning(breaks.c.break_id))
        bid = res.scalar()
        if bid:
            emit(conn, ACTOR, "break_detected" if status == "open" else "break_auto_cleared",
                 reason or f"{break_key} mismatch", inputs={"detection_key": detection_key, "values": values},
                 evidence=[{"source": s} for s in sources], sim_date=d)
        return bool(bid)

    def open_deltas(self, conn, asof: date, exclude: str | None = None) -> dict[tuple[str, str], Decimal]:
        out: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        for r in conn.execute(select(breaks.c.break_id, breaks.c.deltas).where(breaks.c.status == "open")):
            if r.break_id == exclude:
                continue
            for dl in r.deltas:
                if date.fromisoformat(dl["from"]) <= asof:
                    out[(dl["account_id"], dl["asset"])] += D(dl["amount"])
        # corrections already posted for cases still in progress have moved the ledger toward the trusted view
        q = text("""
            SELECT split_part(l.account, ':', 2) AS acct, l.asset, sum(l.amount) AS amt
            FROM ledger_entries e JOIN ledger_lines l ON l.txn_id = e.txn_id
            JOIN proposals p ON p.proposal_id = e.proposal_id JOIN cases c ON c.case_id = p.case_id
            WHERE c.status NOT IN ('completed', 'auto_cleared') AND l.account LIKE 'client:%'
              AND e.settle_date <= :d AND (CAST(:ex AS text) IS NULL OR c.case_id NOT IN (
                  SELECT case_id FROM breaks WHERE break_id = CAST(:ex AS text) AND case_id IS NOT NULL))
            GROUP BY 1, 2""")
        for r in conn.execute(q, {"d": asof, "ex": exclude}):
            out[(r.acct, r.asset)] += r.amt
        return out

    # ------------------------------------------------------------------ transaction level
    def trade_view(self, conn, order_id: str, entries: list[dict] | None = None) -> dict:
        oms = self.src("oms")
        o = oms.order_by_id[order_id]
        ents = entries if entries is not None else ledger.entries_for_refs(conn, [order_id]).get(order_id, [])
        lv = summarize_trade_entries(ents, o.account_id, o.symbol)
        st = self.src("custodian").settle_by_ref.get(order_id)
        execs = self.src("executions").executions_for(order_id)
        seen, exec_qty, dup = set(), D(0), False
        for e in execs:
            if e.exec_type == "cancel":
                continue
            if e.exec_id in seen or e.poss_dup:
                dup = True
                continue
            seen.add(e.exec_id)
            exec_qty += e.fill_qty
        sign = 1 if o.side == "buy" else -1
        return {"order": o, "ledger": lv, "entries": ents, "settlement": st, "exec_qty": exec_qty,
                "dup_report": dup, "sign": sign}

    def _trade_qty_status(self, tv) -> tuple[bool, dict]:
        o, lv, st, sign = tv["order"], tv["ledger"], tv["settlement"], tv["sign"]
        cust_qty = st.qty if st else D(0)
        cust_cash = (st.net_amount - st.commission) if st else D(0)
        broken = lv["qty"] != sign * cust_qty
        deltas = []
        if broken:
            deltas = [
                {"account_id": o.account_id, "asset": o.symbol, "amount": lv["qty"] - sign * cust_qty},
                {"account_id": o.account_id, "asset": "USD", "amount": lv["cash"] - cust_cash},
            ]
        return broken, {"ledger_qty": sign * lv["qty"], "custodian_qty": cust_qty, "executions_qty": tv["exec_qty"],
                        "deltas": deltas, "cash_delta": lv["cash"] - cust_cash}

    def recon_settlements(self, conn, d: date) -> int:
        n = 0
        sts = self.src("custodian").custodian_settlements(d)
        ents = ledger.entries_for_refs(conn, [st.trade_ref for st in sts])
        for st in sts:
            tv = self.trade_view(conn, st.trade_ref, ents.get(st.trade_ref, []))
            o = tv["order"]
            broken, info = self._trade_qty_status(tv)
            if broken:
                settle_from = d.isoformat()
                n += self._create(
                    conn, d, detection_key=f"trade_qty:{o.order_id}", break_key=K_TRADE_QTY, level="transaction",
                    account_id=o.account_id, symbol=o.symbol, ref=o.order_id,
                    sources=["ledger", "custodian", "executions"],
                    values={"ledger": info["ledger_qty"], "custodian": info["custodian_qty"],
                            "executions": info["executions_qty"]},
                    deltas=[{**x, "from": settle_from} for x in info["deltas"]], amount_usd=info["cash_delta"],
                    reason=f"ledger shows {s(info['ledger_qty'])} {o.symbol}, custodian settled {s(info['custodian_qty'])}")
                continue
            diff = tv["ledger"]["principal"] - st.net_amount
            if diff != 0:
                harmless = abs(diff) <= H2_TOLERANCE
                n += self._create(
                    conn, d, detection_key=f"price:{o.order_id}", break_key=K_PRICE, level="transaction",
                    account_id=o.account_id, symbol=o.symbol, ref=o.order_id, sources=["ledger", "custodian"],
                    values={"ledger": tv["ledger"]["principal"], "custodian": st.net_amount}, amount_usd=diff,
                    harmless=harmless, status="auto_cleared" if harmless else "open",
                    reason=(f"rounding difference of ${s(abs(diff))} (within $0.01 tolerance); auto-cleared"
                            if harmless else f"principal differs by {diff}"))
        return n

    def recon_timing(self, conn, d: date) -> None:
        """H1: today's trades await T+1 settlement; yesterday's item clears once they settle."""
        rows = conn.execute(text("""
            SELECT count(*) FROM ledger_entries WHERE kind = 'trade' AND effective_date = :d AND settle_date > :d"""),
                            {"d": d}).scalar()
        if rows:
            self._create(conn, d, detection_key=f"timing:{d}", break_key=K_TIMING, level="transaction",
                         sources=["ledger", "custodian"], values={"ledger_trades": rows, "custodian_settled": 0},
                         harmless=True, reason=f"{rows} trades booked today settle T+1 on {next_bd(d)}")
        for r in conn.execute(select(breaks.c.break_id, breaks.c.vals, breaks.c.detected_on).where(
                breaks.c.break_key == K_TIMING, breaks.c.status == "open", breaks.c.detected_on < d)):
            conn.execute(breaks.update().where(breaks.c.break_id == r.break_id).values(
                status="auto_cleared", resolved_on=d,
                reason=f"{r.vals['ledger_trades']} trades from {r.detected_on} settled on {d}; timing difference only"))
            emit(conn, ACTOR, "break_auto_cleared", f"T+1 settlement timing for {r.detected_on} resolved",
                 inputs={"break_id": r.break_id}, sim_date=d)

    def fee_view(self, conn, order_id: str, entries: list[dict] | None = None) -> dict:
        o = self.src("oms").order_by_id[order_id]
        ents = entries if entries is not None else ledger.entries_for_refs(conn, [order_id]).get(order_id, [])
        fees = self.src("fees")
        sched = fees.fee_schedule_on(o.trade_date)
        prior = fees.fee_schedule_before(o.trade_date)
        tier = fees.tier[o.account_id]
        acct = f"client:{o.account_id}"
        out = []
        for e in [x for x in ents if x["kind"] == "trade"]:
            q = abs(sum((p["amount"] for p in e["postings"] if p["account"] == acct and p["asset"] == o.symbol), D(0)))
            charged = sum((p["amount"] for p in e["postings"] if p["account"] == "revenue:fees"), D(0))
            out.append({"txn_id": e["txn_id"], "qty": q, "charged": charged, "schedule": compute_fee(tier, q, sched),
                        "prior": compute_fee(tier, q, prior) if prior else None})
        corrections = sum((p["amount"] for e in ents if e["kind"] != "trade" for p in e["postings"]
                           if p["account"] == "revenue:fees"), D(0))
        return {"order": o, "tier": tier, "version": sched.version, "prior_version": prior.version if prior else None,
                "bookings": out, "corrections": corrections,
                "net_charged": sum((b["charged"] for b in out), D(0)) + corrections,
                "net_schedule": sum((b["schedule"] for b in out), D(0))}

    def recon_fees(self, conn, d: date) -> int:
        n = 0
        trades = [t for t in self.src("booking").booking_feed(d) if t.kind == "trade"]
        refs = sorted({t.source_ref for t in trades})
        ents = ledger.entries_for_refs(conn, refs)
        for ref in refs:
            fv = self.fee_view(conn, ref, ents.get(ref, []))
            diff = fv["net_charged"] - fv["net_schedule"]
            if diff != 0:
                o = fv["order"]
                n += self._create(
                    conn, d, detection_key=f"fee:{ref}", break_key=K_FEE, level="transaction",
                    account_id=o.account_id, symbol=o.symbol, ref=ref, sources=["ledger", "fee_schedule"],
                    values={"ledger": fv["net_charged"], "fee_schedule": fv["net_schedule"]},
                    deltas=[{"account_id": o.account_id, "asset": "USD", "amount": -diff,
                             "from": next_bd(o.trade_date).isoformat()}],
                    amount_usd=diff, reason=f"commission {s(fv['net_charged'])} vs schedule v{fv['version']} {s(fv['net_schedule'])}")
        return n

    def bank_view(self, conn, transfer_id: str, asof: date) -> dict:
        t = next(x for x in self.src("bank").bank_transfers_asof(asof) if x.transfer_id == transfer_id)
        ents = ledger.entries_for_refs(conn, [transfer_id]).get(transfer_id, [])
        acct = f"client:{t.account_id}"
        net = sum((p["amount"] for e in ents for p in e["postings"] if p["account"] == acct and p["asset"] == "USD"), D(0))
        expected = D(0) if t.status == "returned" else (t.amount if t.direction == "in" else -t.amount)
        return {"transfer": t, "ledger_net": net, "expected": expected, "entries": ents}

    def recon_bank(self, conn, d: date) -> int:
        n = 0
        for t in self.src("bank").bank_transfers_asof(d):
            if t.initiated_on != d and t.status_date != d:
                continue
            bv = self.bank_view(conn, t.transfer_id, d)
            if bv["ledger_net"] != bv["expected"]:
                n += self._create(
                    conn, d, detection_key=f"bank:{t.transfer_id}", break_key=K_BANK, level="transaction",
                    account_id=t.account_id, ref=t.transfer_id, sources=["ledger", "bank"],
                    values={"ledger": bv["ledger_net"], "bank": bv["expected"], "bank_status": t.status,
                            "return_code": t.return_code},
                    deltas=[{"account_id": t.account_id, "asset": "USD", "amount": bv["ledger_net"] - bv["expected"],
                             "from": d.isoformat()}],
                    amount_usd=bv["ledger_net"] - bv["expected"],
                    reason=f"bank shows {t.status}{' ' + t.return_code if t.return_code else ''}; ledger nets {s(bv['ledger_net'])}")
        return n

    # ------------------------------------------------------------------ position and cash level
    def residuals(self, conn, d: date, exclude: str | None = None):
        bal = ledger.client_balances(conn, settled_asof=d)
        cust = self.src("custodian")
        cpos, ccash = cust.custodian_positions(d), cust.custodian_cash(d)
        od = self.open_deltas(conn, d, exclude)
        pos, cash = {}, {}
        keys = {k for k in bal if k[1] != "USD"} | set(cpos)
        for acct, sym in keys:
            r = bal.get((acct, sym), D(0)) - cpos.get((acct, sym), D(0)) - od.get((acct, sym), D(0))
            if r != 0:
                pos[(acct, sym)] = (r, bal.get((acct, sym), D(0)), cpos.get((acct, sym), D(0)))
        accts = {k[0] for k in bal if k[1] == "USD"} | set(ccash)
        for acct in accts:
            r = bal.get((acct, "USD"), D(0)) - ccash.get(acct, D(0)) - od.get((acct, "USD"), D(0))
            if r != 0:
                cash[acct] = (r, bal.get((acct, "USD"), D(0)), ccash.get(acct, D(0)))
        return pos, cash

    def recon_eod(self, conn, d: date) -> int:
        n = 0
        pos, cash = self.residuals(conn, d)
        ref = self.src("reference")
        for (acct, sym), (r, lq, cq) in sorted(pos.items()):
            n += self._create(
                conn, d, detection_key=f"position:{acct}:{sym}:{d}", break_key=K_POS, level="position",
                account_id=acct, symbol=sym, sources=["ledger", "custodian"],
                values={"ledger": lq, "custodian": cq, "unexplained": r},
                deltas=[{"account_id": acct, "asset": sym, "amount": r, "from": d.isoformat()}],
                amount_usd=cents(r * ref.price(d, sym)), reason=f"ledger {s(lq)} {sym} vs custodian {s(cq)}")
        for acct, (r, lc, cc) in sorted(cash.items()):
            n += self._create(
                conn, d, detection_key=f"cash:{acct}:{d}", break_key=K_CASH, level="cash", account_id=acct,
                sources=["ledger", "custodian"], values={"ledger": lc, "custodian": cc, "unexplained": r},
                deltas=[{"account_id": acct, "asset": "USD", "amount": r, "from": d.isoformat()}],
                amount_usd=r, reason=f"ledger cash {s(lc)} vs custodian {s(cc)}")
        return n

    # ------------------------------------------------------------------ entry points
    def run_transaction(self, conn, d: date) -> int:
        return self.recon_settlements(conn, d) + self.recon_fees(conn, d) + self.recon_bank(conn, d)

    def run_day(self, conn, d: date) -> int:
        n = self.run_transaction(conn, d)
        self.recon_timing(conn, d)
        return n + self.recon_eod(conn, d)

    def still_broken(self, conn, b: dict, d: date) -> bool:
        """Verify: rerun recon on one break's keys, ignoring its own delta."""
        key = b["break_key"]
        if key == K_TRADE_QTY:
            return self._trade_qty_status(self.trade_view(conn, b["ref"]))[0]
        if key == K_FEE:
            fv = self.fee_view(conn, b["ref"])
            return fv["net_charged"] != fv["net_schedule"]
        if key == K_BANK:
            bv = self.bank_view(conn, b["ref"], d)
            return bv["ledger_net"] != bv["expected"]
        if key in (K_POS, K_CASH):
            pos, cash = self.residuals(conn, d, exclude=b["break_id"])
            if key == K_POS:
                return (b["account_id"], b["symbol"]) in pos
            return b["account_id"] in cash
        return False
