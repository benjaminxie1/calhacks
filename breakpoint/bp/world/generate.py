"""Seeded true-world generator (R1-R3).

Builds one true world for Meridian Securities, then derives each system's view of
it: OMS orders, broker executions, the booking feed the ledger posts, custodian
settlements / cash / positions, bank transfers, reference data, fee schedule and
directory. All arithmetic is Decimal or integer; the RNG only ever produces ints.
The same seed produces byte-identical files.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from bp import calendar, config
from bp.calendar import business_days, next_bd
from bp.money import D, cents
from bp.models import (
    Account, BankTransfer, CorporateAction, CustodianCashActivity, CustodianSettlement, Execution,
    FeeSchedule, FeeTier, LedgerTxn, Order, Person, Posting, Price, SymbolRef,
)

N_ACCOUNTS = 200
ORDERS_PER_DAY = 300

BASE_PX = {
    "AAPL": "215", "MSFT": "480", "AMZN": "220", "GOOGL": "185", "META": "700", "NVDA": "160",
    "TSLA": "320", "JPM": "285", "V": "350", "JNJ": "165", "WMT": "98", "PG": "160", "MA": "570",
    "HD": "370", "DIS": "120", "BAC": "48", "XOM": "112", "KO": "70", "PFE": "25", "CSCO": "66",
    "PEP": "135", "INTC": "22", "VZ": "43", "T": "28", "ORCL": "230", "NFLX": "1240", "ADBE": "380",
    "CRM": "270", "ABT": "133", "NKE": "72", "MRK": "82", "CVX": "150", "MCD": "300", "COST": "990",
    "IBM": "280", "QCOM": "160", "AMD": "140", "SBUX": "92", "UNH": "310", "CAT": "400",
}

# Corporate actions in the quarter. Splits are the novel moment (N1); dividends feed B4.
SPLITS = [("NFLX", date(2026, 8, 13), "2"), ("COST", date(2026, 10, 15), "2")]
DIVIDENDS = [
    ("PG", date(2026, 7, 21), date(2026, 8, 14), "1.0568"),
    ("XOM", date(2026, 8, 14), date(2026, 9, 10), "1.03"),
    ("CVX", date(2026, 8, 19), date(2026, 9, 10), "1.71"),
    ("JNJ", date(2026, 8, 25), date(2026, 9, 9), "1.30"),
    ("KO", date(2026, 9, 15), date(2026, 10, 1), "0.51"),
]
LOW_INTEREST = {"NFLX": 1, "COST": 1}  # popularity weight (default 10) keeps split holders few

FEES = [
    FeeSchedule(version=1, effective_from=date(2026, 4, 1), tiers=[
        FeeTier(tier="standard", per_trade=D("4.95"), per_share=D("0.005"), min_fee=D("4.95"), max_fee=D("29.95")),
        FeeTier(tier="active", per_trade=D("1.00"), per_share=D("0.0035"), min_fee=D("1.00"), max_fee=D("19.95")),
        FeeTier(tier="premium", per_trade=D("0"), per_share=D("0.002"), min_fee=D("0"), max_fee=D("9.95")),
    ]),
    # The mid-quarter tier change: cheaper standard and active pricing from Aug 17.
    FeeSchedule(version=2, effective_from=date(2026, 8, 17), tiers=[
        FeeTier(tier="standard", per_trade=D("3.95"), per_share=D("0.004"), min_fee=D("3.95"), max_fee=D("24.95")),
        FeeTier(tier="active", per_trade=D("0.50"), per_share=D("0.0025"), min_fee=D("0.50"), max_fee=D("14.95")),
        FeeTier(tier="premium", per_trade=D("0"), per_share=D("0.002"), min_fee=D("0"), max_fee=D("9.95")),
    ]),
]

PEOPLE = [
    Person(role="ops_lead", name="Dana Ortiz", owns="Teaches new fixes; first contact for unknown breaks",
           email="dana.ortiz@meridian.example"),
    Person(role="controller", name="Sam Lee",
           owns="Approves entries over $500, all client money movements, all new playbooks",
           email="sam.lee@meridian.example"),
    Person(role="broker_desk", name="Northgate Execution", owns="Confirms fills, busts, corrections",
           email="desk@northgate.example"),
    Person(role="custodian_ops", name="Harbor Clearing", owns="Confirms positions, settlements, corporate actions",
           email="ops@harborclearing.example"),
    Person(role="client_service", name="Alex Kim", owns="Sends client notices", email="alex.kim@meridian.example"),
    Person(role="client", name="Account holder", owns="Respond to cash calls", reply_sla_days=2,
           email="clients@meridian.example"),
]

FIRST = ["Ava", "Ben", "Chloe", "Diego", "Elena", "Farah", "Gabe", "Hana", "Ivan", "Jada", "Kai", "Lena",
         "Marco", "Nia", "Omar", "Priya", "Quinn", "Rosa", "Sami", "Tara", "Uma", "Victor", "Wen", "Yara", "Zane"]
LAST = ["Abbott", "Brooks", "Chen", "Delgado", "Evans", "Fischer", "Garcia", "Huang", "Ibrahim", "Jensen",
        "Kowalski", "Lopez", "Morales", "Nguyen", "Okafor", "Patel", "Reyes", "Silva", "Tanaka", "Walsh"]


def fee_schedule_on(d: date, schedules=FEES) -> FeeSchedule:
    return [s for s in schedules if s.effective_from <= d][-1]


def compute_fee(tier: str, qty: Decimal, sched: FeeSchedule) -> Decimal:
    t = next(x for x in sched.tiers if x.tier == tier)
    raw = t.per_trade + t.per_share * D(qty)
    return cents(min(max(raw, t.min_fee), t.max_fee))


def acct_key(account_id: str) -> str:
    return f"client:{account_id}"


@dataclass
class World:
    seed: int
    symbols: list[SymbolRef]
    accounts: list[Account]
    prices: list[Price]
    corporate_actions: list[CorporateAction]
    fee_schedules: list[FeeSchedule]
    people: list[Person]
    orders: list[Order] = field(default_factory=list)
    executions: list[Execution] = field(default_factory=list)
    bank_transfers: list[BankTransfer] = field(default_factory=list)
    settlements: list[CustodianSettlement] = field(default_factory=list)
    cash_activity: list[CustodianCashActivity] = field(default_factory=list)
    position_activity: list[dict] = field(default_factory=list)  # custodian corporate-action adjustments
    truth: list[LedgerTxn] = field(default_factory=list)  # correct booking of every true event
    booking: list[LedgerTxn] = field(default_factory=list)  # what the booking system sends (with breaks)
    answer_key: list = field(default_factory=list)


class _Gen:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.seed = seed
        self.n_txn = 0
        self.n_exec = 0
        self.n_ach = 0

    def txn_id(self) -> str:
        self.n_txn += 1
        return f"TXN-{self.n_txn:07d}"


def generate(seed: int = config.SEED) -> World:
    g = _Gen(seed)
    rng = g.rng
    days = business_days()
    syms = json.loads((config.DATA / "symbols.json").read_text())["symbols"]
    symbols = [SymbolRef(**s) for s in syms]
    tickers = [s.symbol for s in symbols]
    weights = [LOW_INTEREST.get(t, 10) for t in tickers]

    accounts = []
    for i in range(1, N_ACCOUNTS + 1):
        r = rng.randint(1, 100)
        tier = "standard" if r <= 60 else "active" if r <= 90 else "premium"
        name = f"{FIRST[rng.randrange(len(FIRST))]} {LAST[rng.randrange(len(LAST))]}"
        accounts.append(Account(account_id=f"ACC-{i:04d}", name=name, tier=tier, opened=date(2026, 6, 15)))
    tier_of = {a.account_id: a.tier for a in accounts}

    # Prices: integer basis-point random walk on Decimal closes; splits divide on the ex-date.
    split_on = {(s, d): D(r) for s, d, r in SPLITS}
    px: dict[tuple[date, str], Decimal] = {}
    for t in tickers:
        p = D(BASE_PX[t])
        for d in days:
            p = cents(p * D(10000 + rng.randint(-180, 190)) / D(10000))
            if (t, d) in split_on:
                p = cents(p / split_on[(t, d)])
            px[(d, t)] = p
    prices = [Price(date=d, symbol=t, close_px=px[(d, t)]) for d in days for t in tickers]

    cas = []
    for i, (s, d, r) in enumerate(SPLITS, 1):
        cas.append(CorporateAction(action_id=f"CA-SPLIT-{i}", symbol=s, type="split", ratio=D(r),
                                   ex_date=d, record_date=d, pay_date=d))
    for i, (s, rec, pay, rate) in enumerate(DIVIDENDS, 1):
        cas.append(CorporateAction(action_id=f"CA-DIV-{i}", symbol=s, type="dividend", rate=D(rate),
                                   ex_date=rec, record_date=rec, pay_date=pay))
    blackout = {(s, d) for s, d, _ in SPLITS} | {(s, next_bd(d, -1)) for s, d, _ in SPLITS}

    w = World(seed=seed, symbols=symbols, accounts=accounts, prices=prices, corporate_actions=cas,
              fee_schedules=FEES, people=PEOPLE)

    # running state
    cash = {a.account_id: D(0) for a in accounts}  # trade-date cash
    hold: dict[str, dict[str, Decimal]] = {a.account_id: {} for a in accounts}  # trade-date holdings
    s_cash = {a.account_id: D(0) for a in accounts}  # settled (custodian) cash
    s_hold: dict[str, dict[str, Decimal]] = {a.account_id: {} for a in accounts}
    pending: dict[date, list] = {}
    div_holders: dict[str, dict[str, Decimal]] = {}
    returns_due: dict[date, list] = {}

    # Two deposits that will bounce (ACH R01) after the client has already spent them (N2).
    risky = {days[29]: "ACC-0137", days[61]: "ACC-0052"}

    def book(d, settle, kind, ref, reason, postings):
        t = LedgerTxn(txn_id=g.txn_id(), date=d, settle_date=settle, kind=kind,
                      postings=[Posting(account=a, asset=s, amount=D(x)) for a, s, x in postings],
                      source_ref=ref, reason=reason)
        w.truth.append(t)
        return t

    def ach(d, acct, direction, amount, status="settled", status_date=None, code=None):
        g.n_ach += 1
        tid = f"ACH-{g.n_ach:06d}"
        w.bank_transfers.append(BankTransfer(transfer_id=tid, account_id=acct, direction=direction, amount=amount,
                                             initiated_on=d, status=status, status_date=status_date or d,
                                             return_code=code))
        sign = 1 if direction == "in" else -1
        w.cash_activity.append(CustodianCashActivity(date=d, account_id=acct, kind="ach_in" if sign > 0 else "ach_out",
                                                     ref=tid, amount=sign * amount))
        book(d, d, "ach", tid, f"ACH {'deposit' if sign > 0 else 'withdrawal'}",
             [(acct_key(acct), "USD", sign * amount), ("firm:omnibus", "USD", -sign * amount)])
        cash[acct] += sign * amount
        s_cash[acct] += sign * amount
        return tid

    n_order = 0
    for di, d in enumerate(days):
        # 1. settlements due today (trades from the previous business day)
        for st, acct, sym, dq, dc in pending.pop(d, []):
            w.settlements.append(st)
            w.cash_activity.append(CustodianCashActivity(date=d, account_id=acct, kind="settlement",
                                                         ref=st.trade_ref, amount=dc))
            s_cash[acct] += dc
            s_hold[acct][sym] = s_hold[acct].get(sym, D(0)) + dq

        # 2. splits on the ex-date (no trades in the symbol the day before or on the day)
        for ca in cas:
            if ca.type == "split" and ca.ex_date == d:
                for acct in sorted(s_hold):
                    q = s_hold[acct].get(ca.symbol, D(0))
                    if q > 0:
                        add = q * (ca.ratio - 1)
                        s_hold[acct][ca.symbol] = q + add
                        hold[acct][ca.symbol] = hold[acct].get(ca.symbol, D(0)) + add
                        w.position_activity.append({"date": d.isoformat(), "account_id": acct, "symbol": ca.symbol,
                                                    "qty": str(add), "ref": ca.action_id, "kind": "split"})
                        book(d, d, "split", f"{ca.action_id}:{acct}", f"{ca.symbol} {ca.ratio}:1 split",
                             [(acct_key(acct), ca.symbol, add), (f"issuer:{ca.symbol}", ca.symbol, -add)])

        # 3. dividends paid today, to holders of record
        for ca in cas:
            if ca.type == "dividend" and ca.pay_date == d:
                for acct, q in sorted(div_holders.get(ca.action_id, {}).items()):
                    amt = cents(ca.rate * q)
                    w.cash_activity.append(CustodianCashActivity(date=d, account_id=acct, kind="dividend",
                                                                 ref=f"{ca.action_id}:{acct}", amount=amt,
                                                                 symbol=ca.symbol))
                    book(d, d, "dividend", f"{ca.action_id}:{acct}", f"{ca.symbol} dividend {ca.rate}/sh on {q}",
                         [(acct_key(acct), "USD", amt), (f"issuer:{ca.symbol}", "USD", -amt)])
                    cash[acct] += amt
                    s_cash[acct] += amt

        # 4. ACH returns arriving today
        for tid, acct, amt in returns_due.pop(d, []):
            w.cash_activity.append(CustodianCashActivity(date=d, account_id=acct, kind="ach_return", ref=tid,
                                                         amount=-amt))
            book(d, d, "ach_return", tid, "ACH deposit returned R01 (insufficient funds)",
                 [(acct_key(acct), "USD", -amt), ("firm:omnibus", "USD", amt)])
            cash[acct] -= amt
            s_cash[acct] -= amt
            if cash[acct] < 0:
                deficit = -cash[acct]
                book(d, d, "receivable", f"{tid}:receivable", "Client owes the returned amount",
                     [(acct_key(acct), "USD", deficit), (f"{acct_key(acct)}:receivable", "USD", -deficit)])
                cash[acct] += deficit

        # 5. money in and out
        if di == 0:
            for a in accounts:
                ach(d, a.account_id, "in", D(rng.randint(50, 400) * 1000))
        else:
            for _ in range(3):
                ach(d, accounts[rng.randrange(N_ACCOUNTS)].account_id, "in", D(rng.randint(1, 20) * 1000))
            for _ in range(2):
                acct = accounts[rng.randrange(N_ACCOUNTS)].account_id
                room = min(cash[acct], s_cash[acct]) - D(5000)
                if room >= 2000 and acct not in risky.values():
                    ach(d, acct, "out", D(rng.randint(1, int(room // 2000)) * 1000))
        forced = []
        if d in risky:
            acct = risky[d]
            amt = D(25000)
            g.n_ach += 1
            tid = f"ACH-{g.n_ach:06d}"
            ret = next_bd(d, 2)
            w.bank_transfers.append(BankTransfer(transfer_id=tid, account_id=acct, direction="in", amount=amt,
                                                 initiated_on=d, status="returned", status_date=ret, return_code="R01"))
            w.cash_activity.append(CustodianCashActivity(date=d, account_id=acct, kind="ach_in", ref=tid, amount=amt))
            book(d, d, "ach", tid, "ACH deposit", [(acct_key(acct), "USD", amt), ("firm:omnibus", "USD", -amt)])
            cash[acct] += amt
            s_cash[acct] += amt
            returns_due.setdefault(ret, []).append((tid, acct, amt))
            forced.append(acct)

        # 6. orders
        n_today = ORDERS_PER_DAY + rng.randint(-30, 30)
        for k in range(n_today + len(forced)):
            force_acct = forced[k - n_today] if k >= n_today else None
            acct = force_acct or accounts[rng.randrange(N_ACCOUNTS)].account_id
            held = sorted(s for s, q in hold[acct].items() if q > 0 and (s, d) not in blackout)
            side = "sell" if held and not force_acct and rng.randint(1, 100) <= 45 else "buy"
            if side == "sell":
                sym = held[rng.randrange(len(held))]
                p = px[(d, sym)]
                have = hold[acct][sym]
                qty = have if rng.randint(1, 3) == 1 else max(D(1), (have * D(rng.randint(2, 8)) / D(10)).to_integral_value())
            else:
                sym = rng.choices(tickers, weights=weights)[0]
                if (sym, d) in blackout:
                    continue
                p = px[(d, sym)]
                notional = D(rng.randint(10, 300) * 100) if not force_acct else cash[acct] - D(500)
                budget = min(notional, cash[acct] - D(50))
                if budget <= p:
                    continue
                qty = (budget / p).to_integral_value(rounding="ROUND_FLOOR")
                if qty >= 50:
                    qty = (qty / 10).to_integral_value(rounding="ROUND_FLOOR") * 10
            n_order += 1
            oid = f"ORD-{n_order:06d}"
            otype = "limit" if rng.randint(1, 100) <= 30 and not force_acct else "market"
            fill_px = cents(p * D(10000 + rng.randint(-30, 30)) / D(10000))
            limit_px = None
            if otype == "limit":
                limit_px = cents(p * D(10000 + rng.randint(-60, 100) * (1 if side == "buy" else -1)) / D(10000))
            ts = f"{d.isoformat()}T{rng.randint(9, 15):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}"
            if otype == "limit" and ((side == "buy" and fill_px > limit_px) or (side == "sell" and fill_px < limit_px)):
                w.orders.append(Order(order_id=oid, account_id=acct, symbol=sym, side=side, qty=qty, order_type=otype,
                                      limit_px=limit_px, submitted_at=ts, trade_date=d, status="cancelled"))
                continue
            exec_qty = qty
            if qty >= 20 and not force_acct and rng.randint(1, 100) <= 4:
                exec_qty = max(D(10), (qty * D(rng.randint(3, 9)) / D(10) / 10).to_integral_value(rounding="ROUND_FLOOR") * 10)
                if exec_qty >= qty:
                    exec_qty = qty - 10
            status = "filled" if exec_qty == qty else "partially_filled"
            w.orders.append(Order(order_id=oid, account_id=acct, symbol=sym, side=side, qty=qty, order_type=otype,
                                  limit_px=limit_px, submitted_at=ts, trade_date=d, status=status))
            fills = [exec_qty]
            if exec_qty >= 20 and rng.randint(1, 100) <= 20:
                first = (exec_qty / 2).to_integral_value(rounding="ROUND_FLOOR")
                fills = [first, exec_qty - first]
            gross = D(0)
            for j, fq in enumerate(fills):
                g.n_exec += 1
                fpx = fill_px if j == 0 else cents(fill_px + D(rng.randint(-5, 5)) / D(100))
                if otype == "limit":
                    fpx = min(fpx, limit_px) if side == "buy" else max(fpx, limit_px)
                gross += cents(fq * fpx)
                w.executions.append(Execution(exec_id=f"EX-{g.n_exec:07d}", order_id=oid, fill_qty=fq, fill_px=fpx,
                                              venue=["NSDQ", "NYSE", "ARCA", "IEX"][rng.randrange(4)],
                                              exec_time=ts, trade_date=d, exec_type="new"))
            if status == "partially_filled":
                g.n_exec += 1
                w.executions.append(Execution(exec_id=f"EX-{g.n_exec:07d}", order_id=oid, fill_qty=D(0), fill_px=D(0),
                                              venue="NSDQ", exec_time=ts, trade_date=d, exec_type="cancel"))
            fee = compute_fee(tier_of[acct], exec_qty, fee_schedule_on(d))
            sign = -1 if side == "buy" else 1
            settle = next_bd(d)
            book(d, settle, "trade", oid, f"{side} {exec_qty} {sym}", [
                (acct_key(acct), "USD", sign * gross), ("street", "USD", -sign * gross),
                (acct_key(acct), sym, -sign * exec_qty), ("street", sym, sign * exec_qty),
                (acct_key(acct), "USD", -fee), ("revenue:fees", "USD", fee),
            ])
            cash[acct] += sign * gross - fee
            hold[acct][sym] = hold[acct].get(sym, D(0)) - sign * exec_qty
            st = CustodianSettlement(trade_ref=oid, account_id=acct, symbol=sym, side=side, qty=exec_qty,
                                     settle_date=settle, net_amount=sign * gross, commission=fee)
            pending.setdefault(settle, []).append((st, acct, sym, -sign * exec_qty, sign * gross - fee))

        # 7. end of day: holders of record for dividends recorded today (settled positions)
        for ca in cas:
            if ca.type == "dividend" and ca.record_date == d:
                div_holders[ca.action_id] = {a: h[ca.symbol] for a, h in s_hold.items() if h.get(ca.symbol, D(0)) > 0}

    # trades from the last day settle after the window; keep their settlement rows (future-dated)
    for settle in sorted(pending):
        for st, *_ in pending[settle]:
            w.settlements.append(st)
    w.booking = list(w.truth)
    return w


# ------------------------------------------------------------------ files

FILES = [
    "accounts.jsonl", "symbols.jsonl", "prices.jsonl", "corporate_actions.jsonl", "fee_schedules.jsonl",
    "people.jsonl", "orders.jsonl", "executions.jsonl", "bank_transfers.jsonl", "custodian_settlements.jsonl",
    "custodian_cash_activity.jsonl", "custodian_position_activity.jsonl", "booking.jsonl", "truth.jsonl",
    "answer_key.jsonl",
]


def _line(x) -> str:
    obj = x if isinstance(x, dict) else x.model_dump(mode="json")
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def write(w: World, out: Path) -> dict[str, str]:
    out.mkdir(parents=True, exist_ok=True)
    groups = {
        "accounts.jsonl": w.accounts, "symbols.jsonl": w.symbols, "prices.jsonl": w.prices,
        "corporate_actions.jsonl": w.corporate_actions, "fee_schedules.jsonl": w.fee_schedules,
        "people.jsonl": w.people, "orders.jsonl": w.orders, "executions.jsonl": w.executions,
        "bank_transfers.jsonl": w.bank_transfers, "custodian_settlements.jsonl": w.settlements,
        "custodian_cash_activity.jsonl": w.cash_activity, "custodian_position_activity.jsonl": w.position_activity,
        "booking.jsonl": w.booking, "truth.jsonl": w.truth, "answer_key.jsonl": w.answer_key,
    }
    hashes = {}
    for name in FILES:
        body = "".join(_line(x) + "\n" for x in groups[name])
        (out / name).write_text(body)
        hashes[name] = hashlib.sha256(body.encode()).hexdigest()
    (out / "manifest.json").write_text(json.dumps({"seed": w.seed, "hashes": hashes}, indent=1, sort_keys=True))
    return hashes


def build(seed: int = config.SEED, clean: bool = False, out: Path | None = None) -> tuple[World, dict]:
    from bp.world.inject import inject

    w = generate(seed)
    if not clean:
        inject(w)
    return w, write(w, out or config.world_dir(seed, clean))


if __name__ == "__main__":
    import argparse
    import time

    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=config.SEED)
    ap.add_argument("--clean", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    w, h = build(a.seed, a.clean)
    print(f"world seed={a.seed} clean={a.clean}: {len(w.orders)} orders, {len(w.executions)} executions, "
          f"{len(w.booking)} booking txns, {len(w.answer_key)} planted breaks in {time.time() - t0:.1f}s")
    print(f"written to {config.world_dir(a.seed, a.clean)}")
