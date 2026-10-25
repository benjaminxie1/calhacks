"""Ledger and accuracy: T6-T10, T19."""
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from bp.ledger import service as ledger
from bp.money import D

E = lambda amt=D("100"): [  # noqa: E731
    {"account": "lease:LSE-001:prepaid", "asset": "USD", "amount": amt},
    {"account": "cash:operating", "asset": "USD", "amount": -amt},
]


def _post(c, key="k1", day=date(2026, 2, 3), postings=None, cred=None):
    return ledger.post(c, cred or ledger.issue_credential("executor"), txn_id=f"T-{key}", idempotency_key=key,
                       effective_date=day, settle_date=day, kind="test", source_ref="ref", reason="test",
                       actor="executor", postings=postings or E())


def test_T6_balanced_entries(db):
    """R7: debits equal credits per asset; an unbalanced entry is rejected (in code and by the database)."""
    with db.begin() as c:
        _post(c)
    with pytest.raises(ledger.LedgerError):
        with db.begin() as c:
            _post(c, "k2", postings=[{"account": "lease:LSE-001:payable", "asset": "USD", "amount": D("5")},
                                     {"account": "lease:LSE-001:expense", "asset": "USD", "amount": D("-4")}])
    # bypass the service: the deferred trigger still refuses at commit
    with pytest.raises(DBAPIError, match="does not balance"):
        with db.begin() as c:
            c.execute(text("INSERT INTO ledger_entries (txn_id, idempotency_key, effective_date, settle_date, period, kind,"
                           " source_ref, reason, actor) VALUES ('RAW', 'raw', '2026-02-03', '2026-02-03', '2026-02', 'x',"
                           " 'x', 'x', 'x')"))
            c.execute(text("INSERT INTO ledger_lines (txn_id, account, asset, amount) VALUES ('RAW', 'a', 'USD', 1)"))
    with db.connect() as c:
        assert ledger.trial_balance(c) == {"USD": 0}


def test_T7_trial_balance_every_day(quarter):
    """R7: the trial balance sums to zero after every replay day."""
    assert len(quarter["trial_balance"]) == 83
    assert all(v == {} for v in quarter["trial_balance"].values())


def test_T8_immutability(db):
    """R8: updating or deleting a posted entry is rejected; corrections are new entries."""
    with db.begin() as c:
        _post(c)
    for sql in ["UPDATE ledger_lines SET amount = 1", "DELETE FROM ledger_lines", "UPDATE ledger_entries SET reason='x'",
                "DELETE FROM ledger_entries"]:
        with pytest.raises(DBAPIError, match="append-only"):
            with db.begin() as c:
                c.execute(text(sql))


def test_T9_period_lock(db):
    """R9: posting into closed March is rejected; the fix lands in April with a reference."""
    with db.begin() as c:
        orig, _ = _post(c, "mar", day=date(2026, 3, 16))
        closed = ledger.close_due_periods(c, date(2026, 4, 6))  # March locks on its 3rd business day of April
    assert "2026-03" in closed and "2026-02" in closed and "2026-04" not in closed
    with pytest.raises(ledger.LedgerError, match="closed"):
        with db.begin() as c:
            _post(c, "late", day=date(2026, 3, 31))
    with pytest.raises(DBAPIError, match="closed"):  # the database refuses too
        with db.begin() as c:
            c.execute(text("INSERT INTO ledger_entries (txn_id, idempotency_key, effective_date, settle_date, period, kind,"
                           " source_ref, reason, actor) VALUES ('X', 'x', '2026-03-31', '2026-03-31', '2026-03', 'x', 'x',"
                           " 'x', 'x')"))
    with db.begin() as c:
        fix, _ = ledger.post(c, ledger.issue_credential("executor"), txn_id="T-fix", idempotency_key="fix",
                             effective_date=date(2026, 4, 6), settle_date=date(2026, 4, 6), kind="correction",
                             source_ref="ref", reason="late fix", actor="executor", postings=E(D("-100")),
                             references_txn=orig)
        e = ledger.entry(c, fix)
    assert e["period"] == "2026-04" and e["references_txn"] == orig


def test_T10_idempotency(db):
    """R10: executing the same approved proposal twice yields one posting and one transfer."""
    from bp.agents import approvals
    from bp.agents.executor import Executor
    from bp.models import Action, Posting, Proposal

    calc = {"statement_id": "STMT-LSE-001-2025", "lease_id": "LSE-001", "paid": D("31200"), "credited": D("28600")}
    from bp import calculators
    out = calculators.run("estimates_credit", calc)
    p = Proposal(proposal_id="PRP-T10", case_id="CASE-T", action_name="estimates_credit",
                 action=Action(type="journal_entry", template="estimates_credit"),
                 postings=[Posting(**x) for x in out["postings"]], amount_usd=out["amount_usd"],
                 calculator="estimates_credit", calc_inputs={k: str(v) for k, v in calc.items()},
                 source_ref="estimates:STMT-LSE-001-2025", memo="t", prepared_by="preparer-agent")
    d = date(2026, 2, 3)
    ex = Executor()
    with db.begin() as c:
        approvals.create(c, p, d)
        approvals.decide(c, "PRP-T10", approvals.CONTROLLER, "approved", "ok", d)
        ex.execute(c, "PRP-T10", d)
        ex.execute(c, "PRP-T10", d)
        assert c.execute(text("SELECT count(*) FROM ledger_entries WHERE proposal_id='PRP-T10'")).scalar() == 1
    cred = ledger.issue_credential("executor")
    with db.begin() as c:
        for _ in range(2):
            ledger.send_transfer(c, cred, transfer_id="OUT-1", idempotency_key="xfer-1", account_id="LSE-001",
                                 direction="out", amount=D("10"), sent_on=d)
        assert c.execute(text("SELECT count(*) FROM bank_outbound")).scalar() == 1
        assert ledger.post_feed(c, ledger.issue_credential("booking-system"), []) == 0


def test_T19_write_isolation(db):
    """R16: any non-executor attempt to post or transfer fails for lack of credentials."""
    for agent in ["investigator", "preparer", "outreach", "approver", "learner", "auditor", "matcher"]:
        with pytest.raises(ledger.CredentialDenied):
            ledger.issue_credential(agent)
    with pytest.raises(ledger.CredentialDenied):
        with db.begin() as c:
            _post(c, cred="forged-token")
    with pytest.raises(ledger.CredentialDenied):
        with db.begin() as c:
            ledger.send_transfer(c, None, transfer_id="X", idempotency_key="x", account_id="LSE-001", direction="out",
                                 amount=D("1"), sent_on=date(2026, 2, 3))
