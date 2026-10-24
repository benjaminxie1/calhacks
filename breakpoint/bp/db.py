"""Postgres schema: ledger (system of record) and app state.

All money and quantity columns are NUMERIC (R6). Posted entries and the event log
are append-only, enforced by triggers (R8, R24). Every entry must balance per asset
at commit (R7). Closed periods reject postings (R9).
"""
from __future__ import annotations

from functools import lru_cache

from sqlalchemy import (
    BigInteger, Boolean, Column, Date, ForeignKey, Integer, MetaData, Numeric, Table, Text,
    create_engine, text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

from bp import config

N = lambda: Numeric(24, 6)  # noqa: E731
md = MetaData()

ledger_entries = Table(
    "ledger_entries", md,
    Column("seq", BigInteger, primary_key=True, autoincrement=True),
    Column("txn_id", Text, unique=True, nullable=False),
    Column("idempotency_key", Text, unique=True, nullable=False),
    Column("effective_date", Date, nullable=False),
    Column("settle_date", Date, nullable=False),
    Column("period", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("source_ref", Text, nullable=False, index=True),
    Column("reason", Text, nullable=False),
    Column("actor", Text, nullable=False),
    Column("references_txn", Text),
    Column("proposal_id", Text),
    Column("meta", JSONB, nullable=False, server_default="{}"),
)

ledger_lines = Table(
    "ledger_lines", md,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("txn_id", Text, ForeignKey("ledger_entries.txn_id"), nullable=False, index=True),
    Column("account", Text, nullable=False, index=True),
    Column("asset", Text, nullable=False),
    Column("amount", N(), nullable=False),
)

periods = Table(
    "periods", md,
    Column("period", Text, primary_key=True),
    Column("status", Text, nullable=False),
    Column("closed_on", Date),
)

bank_outbound = Table(  # transfers the executor sends through the (mock) bank
    "bank_outbound", md,
    Column("transfer_id", Text, primary_key=True),
    Column("idempotency_key", Text, unique=True, nullable=False),
    Column("account_id", Text, nullable=False),
    Column("direction", Text, nullable=False),
    Column("amount", N(), nullable=False),
    Column("sent_on", Date, nullable=False),
    Column("proposal_id", Text),
)

breaks = Table(
    "breaks", md,
    Column("break_id", Text, primary_key=True),
    Column("detection_key", Text, unique=True, nullable=False),
    Column("break_key", Text, nullable=False),
    Column("level", Text, nullable=False),
    Column("account_id", Text),
    Column("symbol", Text),
    Column("ref", Text),
    Column("sources", JSONB, nullable=False),
    Column("vals", JSONB, nullable=False),
    Column("deltas", JSONB, nullable=False, server_default="{}"),
    Column("amount_usd", N(), nullable=False),
    Column("detected_on", Date, nullable=False),
    Column("status", Text, nullable=False),
    Column("harmless", Boolean, nullable=False, server_default="false"),
    Column("reason", Text),
    Column("case_id", Text, index=True),
    Column("resolved_on", Date),
)

cases = Table(
    "cases", md,
    Column("case_id", Text, primary_key=True),
    Column("break_key", Text, nullable=False),
    Column("group_ref", Text, unique=True, nullable=False),
    Column("status", Text, nullable=False),
    Column("opened_on", Date, nullable=False),
    Column("updated_on", Date, nullable=False),
    Column("closed_on", Date),
    Column("playbook_id", Text),
    Column("playbook_version", Integer),
    Column("branch_id", Text),
    Column("facts", JSONB, nullable=False, server_default="{}"),
    Column("notes", JSONB, nullable=False, server_default="[]"),
    Column("summary", Text),
    Column("waiting_on", Text),
    Column("waiting_since", Date),
    Column("escalated", Boolean, nullable=False, server_default="false"),
    Column("completion", JSONB),
    Column("pending_actions", JSONB, nullable=False, server_default="[]"),
    Column("amount_usd", N(), nullable=False, server_default="0"),
)

proposals = Table(
    "proposals", md,
    Column("proposal_id", Text, primary_key=True),
    Column("case_id", Text, nullable=False, index=True),
    Column("body", JSONB, nullable=False),
    Column("hash", Text, nullable=False),
    Column("amount_usd", N(), nullable=False),
    Column("prepared_by", Text, nullable=False),
    Column("status", Text, nullable=False),  # pending | approved | rejected | executed | failed
    Column("route", Text),  # approver | controller
    Column("created_on", Date, nullable=False),
)

approvals = Table(
    "approvals", md,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("proposal_id", Text, nullable=False, index=True),
    Column("proposal_hash", Text, nullable=False),
    Column("decided_by", Text, nullable=False),
    Column("decision", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("decided_on", Date, nullable=False),
)

playbooks = Table(
    "playbooks", md,
    Column("id", Text, primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("status", Text, nullable=False),
    Column("key_name", Text, nullable=False, index=True),
    Column("body", JSONB, nullable=False),
    Column("created_on", Date),
    Column("created_by", Text, nullable=False),
    Column("approved_by", Text),
    Column("note", Text),
)

events = Table(
    "events", md,
    Column("seq", BigInteger, primary_key=True, autoincrement=True),
    Column("ts", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("sim_date", Date),
    Column("actor", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("case_id", Text, index=True),
    Column("inputs", JSONB, nullable=False, server_default="{}"),
    Column("evidence", JSONB, nullable=False, server_default="[]"),
    Column("reason", Text, nullable=False),
)

messages = Table(
    "messages", md,
    Column("msg_id", Text, primary_key=True),
    Column("case_id", Text, index=True),
    Column("from_party", Text, nullable=False),
    Column("to_role", Text, nullable=False),
    Column("to_name", Text, nullable=False),
    Column("kind", Text, nullable=False),  # notify | request | follow_up | escalation | reply
    Column("body", Text, nullable=False),
    Column("sent_on", Date, nullable=False),
    Column("reply_to", Text),
    Column("answered", Boolean, nullable=False, server_default="false"),
    Column("meta", JSONB, nullable=False, server_default="{}"),
)

teach_sessions = Table(
    "teach_sessions", md,
    Column("id", Text, primary_key=True),
    Column("case_id", Text, nullable=False),
    Column("teacher", Text, nullable=False),
    Column("trusted_source", Text, nullable=False),
    Column("actions", JSONB, nullable=False),
    Column("fact", Text, nullable=False),
    Column("note", Text, nullable=False),
    Column("created_on", Date, nullable=False),
    Column("draft_playbook", Text),
    Column("draft_version", Integer),
)

injections = Table(
    "injections", md,
    Column("id", Text, primary_key=True),
    Column("effective_on", Date, nullable=False),
    Column("spec", JSONB, nullable=False),
    Column("records", JSONB, nullable=False),
    Column("answer_key", JSONB, nullable=False),
)

sim_state = Table(
    "sim_state", md,
    Column("id", Integer, primary_key=True),
    Column("seed", Integer, nullable=False),
    Column("clean", Boolean, nullable=False),
    Column("current_date", Date),
    Column("last_done", Date),
    Column("running", Boolean, nullable=False, server_default="false"),
    Column("speed", Text, nullable=False, server_default="fast"),
    Column("controller_mode", Text, nullable=False, server_default="scripted"),
    Column("teach_mode", Text, nullable=False, server_default="human"),
)

TRIGGERS = """
CREATE OR REPLACE FUNCTION bp_append_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: % rejected', TG_TABLE_NAME, TG_OP USING ERRCODE = 'P0001';
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS ledger_entries_immutable ON ledger_entries;
CREATE TRIGGER ledger_entries_immutable BEFORE UPDATE OR DELETE ON ledger_entries
  FOR EACH ROW EXECUTE FUNCTION bp_append_only();
DROP TRIGGER IF EXISTS ledger_lines_immutable ON ledger_lines;
CREATE TRIGGER ledger_lines_immutable BEFORE UPDATE OR DELETE ON ledger_lines
  FOR EACH ROW EXECUTE FUNCTION bp_append_only();
DROP TRIGGER IF EXISTS events_immutable ON events;
CREATE TRIGGER events_immutable BEFORE UPDATE OR DELETE ON events
  FOR EACH ROW EXECUTE FUNCTION bp_append_only();

CREATE OR REPLACE FUNCTION bp_period_open() RETURNS trigger AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM periods WHERE period = NEW.period AND status = 'closed') THEN
    RAISE EXCEPTION 'period % is closed; post the fix in the open period with a reference', NEW.period
      USING ERRCODE = 'P0002';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS ledger_period_open ON ledger_entries;
CREATE TRIGGER ledger_period_open BEFORE INSERT ON ledger_entries
  FOR EACH ROW EXECUTE FUNCTION bp_period_open();

CREATE OR REPLACE FUNCTION bp_entry_balanced() RETURNS trigger AS $$
DECLARE bad record;
BEGIN
  SELECT asset, sum(amount) AS s INTO bad FROM ledger_lines
    WHERE txn_id = NEW.txn_id GROUP BY asset HAVING sum(amount) <> 0 LIMIT 1;
  IF FOUND THEN
    RAISE EXCEPTION 'entry % does not balance in %: off by %', NEW.txn_id, bad.asset, bad.s
      USING ERRCODE = 'P0003';
  END IF;
  RETURN NULL;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS ledger_entry_balanced ON ledger_entries;
CREATE CONSTRAINT TRIGGER ledger_entry_balanced AFTER INSERT ON ledger_entries
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bp_entry_balanced();
"""


@lru_cache(maxsize=4)
def engine(url: str | None = None):
    return create_engine(url or config.DATABASE_URL, future=True, pool_pre_ping=True)


def init_schema(eng=None, drop: bool = False) -> None:
    eng = eng or engine()
    if drop:
        with eng.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    md.create_all(eng)
    with eng.begin() as c:
        c.execute(text(TRIGGERS))


def reset(eng=None) -> None:
    """Wipe all state: drop and recreate the schema (the append-only triggers block row deletes)."""
    init_schema(eng or engine(), drop=True)
