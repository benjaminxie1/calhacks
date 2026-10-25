"""Double-entry ledger on Postgres (Formance-style: accounts hold balances per asset).

Write access needs a credential. Only two components are ever issued one: the
upstream AP booking system (which posts Golden Wok's own feed during replay) and the
executor. Agents never get one (R16). Every entry balances per asset (R7), posted
entries are immutable (R8), closed periods reject postings (R9), and every posting
carries an idempotency key so a replay never double-posts (R10).
"""
from __future__ import annotations

import secrets
from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from bp.calendar import lock_date, period_of
from bp.db import bank_outbound, ledger_entries, ledger_lines, periods
from bp.money import D

CHART = {
    "lease:{id}:expense": "CAM expense booked for the lease (operating expenses, taxes, fees)",
    "lease:{id}:prepaid": "CAM estimates paid to the landlord, not yet applied by a reconciliation",
    "lease:{id}:payable": "what Golden Wok owes the landlord (negative balance = owed; positive = landlord owes a credit)",
    "cash:operating": "operating bank account (offset for estimate payments)",
}

_ISSUED: dict[str, str] = {}
_WRITERS = {"executor", "booking-system"}


class CredentialDenied(PermissionError):
    pass


class LedgerError(ValueError):
    pass


def issue_credential(component: str) -> str:
    if component not in _WRITERS:
        raise CredentialDenied(f"{component} may not hold ledger or bank credentials")
    if component not in _ISSUED:
        _ISSUED[component] = secrets.token_hex(16)
    return _ISSUED[component]


def _check(credential: str | None) -> str:
    for comp, tok in _ISSUED.items():
        if credential and secrets.compare_digest(credential, tok):
            return comp
    raise CredentialDenied("ledger write rejected: missing or invalid credential")


def ensure_periods(conn, months=("2026-02", "2026-03", "2026-04", "2026-05")) -> None:
    conn.execute(insert(periods).values([{"period": m, "status": "open"} for m in months])
                 .on_conflict_do_nothing())


def close_period(conn, period: str, on: date) -> None:
    conn.execute(periods.update().where(periods.c.period == period).values(status="closed", closed_on=on))


def close_due_periods(conn, today: date) -> list[str]:
    closed = []
    for row in conn.execute(select(periods.c.period).where(periods.c.status == "open")):
        if lock_date(row.period) <= today:
            close_period(conn, row.period, today)
            closed.append(row.period)
    return closed


def open_periods(conn) -> list[str]:
    return [r.period for r in conn.execute(select(periods.c.period).where(periods.c.status == "open"))]


def validate(postings: list[dict]) -> None:
    if not postings:
        raise LedgerError("entry has no postings")
    sums: dict[str, Decimal] = defaultdict(Decimal)
    for p in postings:
        amt = p["amount"]
        if isinstance(amt, float):
            raise LedgerError("float amount rejected (R6)")
        sums[p["asset"]] += D(amt)
    bad = {a: s for a, s in sums.items() if s != 0}
    if bad:
        raise LedgerError(f"entry does not balance per asset: {bad}")


def post(conn, credential: str, *, txn_id: str, idempotency_key: str, effective_date: date, settle_date: date,
         kind: str, source_ref: str, reason: str, actor: str, postings: list[dict],
         references_txn: str | None = None, proposal_id: str | None = None, meta: dict | None = None) -> tuple[str, bool]:
    """Post one entry. Returns (txn_id, created). Re-posting the same idempotency key is a no-op."""
    writer = _check(credential)
    validate(postings)
    existing = conn.execute(select(ledger_entries.c.txn_id).where(
        ledger_entries.c.idempotency_key == idempotency_key)).scalar()
    if existing:
        return existing, False
    period = period_of(effective_date)
    status = conn.execute(select(periods.c.status).where(periods.c.period == period)).scalar()
    if status == "closed":
        raise LedgerError(f"period {period} is closed; post in the open period with a reference")
    conn.execute(ledger_entries.insert().values(
        txn_id=txn_id, idempotency_key=idempotency_key, effective_date=effective_date, settle_date=settle_date,
        period=period, kind=kind, source_ref=source_ref, reason=reason, actor=actor,
        references_txn=references_txn, proposal_id=proposal_id, meta={**(meta or {}), "writer": writer}))
    conn.execute(ledger_lines.insert(), [
        {"txn_id": txn_id, "account": p["account"], "asset": p["asset"], "amount": D(p["amount"])} for p in postings])
    return txn_id, True


def post_feed(conn, credential: str, txns: list) -> int:
    """Bulk-post the booking system's feed for a day (idempotent on txn_id)."""
    writer = _check(credential)
    if not txns:
        return 0
    keys = [f"feed:{t.txn_id}" for t in txns]
    have = set(conn.execute(select(ledger_entries.c.idempotency_key).where(
        ledger_entries.c.idempotency_key.in_(keys))).scalars())
    new = [t for t in txns if f"feed:{t.txn_id}" not in have]
    if not new:
        return 0
    for t in new:
        validate([p.model_dump() for p in t.postings])
    conn.execute(ledger_entries.insert(), [{
        "txn_id": t.txn_id, "idempotency_key": f"feed:{t.txn_id}", "effective_date": t.date,
        "settle_date": t.settle_date, "period": period_of(t.date), "kind": t.kind, "source_ref": t.source_ref,
        "reason": t.reason, "actor": t.actor, "references_txn": None, "proposal_id": None,
        "meta": {"writer": writer}} for t in new])
    conn.execute(ledger_lines.insert(), [
        {"txn_id": t.txn_id, "account": p.account, "asset": p.asset, "amount": p.amount}
        for t in new for p in t.postings])
    return len(new)


# ------------------------------------------------------------------ reads (no credential needed)

def trial_balance(conn) -> dict[str, Decimal]:
    rows = conn.execute(select(ledger_lines.c.asset, func.sum(ledger_lines.c.amount)).group_by(ledger_lines.c.asset))
    return {a: s for a, s in rows}


def balances(conn, prefix: str = "lease:") -> dict[tuple[str, str], Decimal]:
    """(account, asset) -> balance for every account under the prefix."""
    q = text("SELECT account, asset, sum(amount) AS bal FROM ledger_lines WHERE account LIKE :p GROUP BY 1, 2")
    return {(r.account, r.asset): r.bal for r in conn.execute(q, {"p": prefix + "%"})}


def corrections_for(conn, source_ref: str) -> Decimal:
    """Total credited to a lease's payable by executed corrections for one finding (its detection key)."""
    q = text("""SELECT coalesce(sum(l.amount), 0) FROM ledger_entries e JOIN ledger_lines l ON l.txn_id = e.txn_id
                WHERE e.kind = 'correction' AND e.source_ref = :r AND l.account LIKE :pat""")
    return conn.execute(q, {"r": source_ref, "pat": "%:payable"}).scalar()


def entries_for_refs(conn, refs: list[str]) -> dict[str, list[dict]]:
    if not refs:
        return {}
    q = text("""
        SELECT e.txn_id, e.source_ref, e.kind, e.effective_date, e.settle_date, e.actor, e.references_txn,
               l.account, l.asset, l.amount
        FROM ledger_entries e JOIN ledger_lines l ON l.txn_id = e.txn_id
        WHERE e.source_ref = ANY(:refs) ORDER BY e.seq, l.id""")
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in conn.execute(q, {"refs": refs}):
        ent = out[r.source_ref].setdefault(r.txn_id, {
            "txn_id": r.txn_id, "kind": r.kind, "date": r.effective_date, "settle_date": r.settle_date,
            "actor": r.actor, "references_txn": r.references_txn, "postings": []})
        ent["postings"].append({"account": r.account, "asset": r.asset, "amount": r.amount})
    return {k: list(v.values()) for k, v in out.items()}


def entry(conn, txn_id: str) -> dict | None:
    e = conn.execute(select(ledger_entries).where(ledger_entries.c.txn_id == txn_id)).mappings().first()
    if not e:
        return None
    lines = conn.execute(select(ledger_lines).where(ledger_lines.c.txn_id == txn_id).order_by(ledger_lines.c.id))
    return {**dict(e), "postings": [{"account": r.account, "asset": r.asset, "amount": r.amount} for r in lines]}


# ------------------------------------------------------------------ mock bank (Increase fallback)

def send_transfer(conn, credential: str, *, transfer_id: str, idempotency_key: str, account_id: str,
                  direction: str, amount: Decimal, sent_on: date, proposal_id: str | None = None) -> tuple[str, bool]:
    _check(credential)
    if isinstance(amount, float):
        raise LedgerError("float amount rejected (R6)")
    res = conn.execute(insert(bank_outbound).values(
        transfer_id=transfer_id, idempotency_key=idempotency_key, account_id=account_id, direction=direction,
        amount=amount, sent_on=sent_on, proposal_id=proposal_id).on_conflict_do_nothing(
        index_elements=["idempotency_key"]).returning(bank_outbound.c.transfer_id))
    tid = res.scalar()
    if tid:
        return tid, True
    return conn.execute(select(bank_outbound.c.transfer_id).where(
        bank_outbound.c.idempotency_key == idempotency_key)).scalar(), False
