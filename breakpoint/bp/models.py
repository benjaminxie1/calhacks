"""Contracts for every source and every domain object.

Money and quantities are Decimal everywhere (R6). Each source model mirrors one
system's view of the world; none of them is the truth.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Side = Literal["buy", "sell"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- sources (1-8)

class Order(Model):  # 1. OMS
    order_id: str
    account_id: str
    symbol: str
    side: Side
    qty: Decimal
    order_type: Literal["market", "limit"]
    limit_px: Decimal | None = None
    submitted_at: str  # ISO timestamp, simulated
    trade_date: date
    status: Literal["filled", "partially_filled", "cancelled"]


class Execution(Model):  # 2. Executing broker (FIX-style)
    exec_id: str
    order_id: str
    fill_qty: Decimal
    fill_px: Decimal
    venue: str
    exec_time: str
    trade_date: date
    exec_type: Literal["new", "cancel", "correct"]
    poss_dup: bool = False  # FIX PossDupFlag: a re-sent report


class Posting(Model):
    account: str
    asset: str
    amount: Decimal


class LedgerTxn(Model):  # 3. Internal ledger (what the booking system sends to post)
    txn_id: str
    date: date
    settle_date: date
    kind: str
    postings: list[Posting]
    source_ref: str
    reason: str
    actor: str = "booking-system"


class CustodianPosition(Model):  # 4. Custodian / clearing statement
    date: date
    account_id: str
    symbol: str
    qty: Decimal


class CustodianCash(Model):
    date: date
    account_id: str
    balance: Decimal


class CustodianSettlement(Model):
    trade_ref: str
    account_id: str
    symbol: str
    side: Side
    qty: Decimal
    settle_date: date
    net_amount: Decimal  # gross principal, signed from the client's view (buy < 0)
    commission: Decimal


class CustodianCashActivity(Model):
    date: date
    account_id: str
    kind: Literal["dividend", "ach_in", "ach_out", "ach_return", "settlement"]
    ref: str
    amount: Decimal
    symbol: str | None = None


class BankTransfer(Model):  # 5. Bank (firm account + client ACH)
    transfer_id: str
    account_id: str
    direction: Literal["in", "out"]
    amount: Decimal
    initiated_on: date
    status: Literal["pending", "settled", "returned"]
    status_date: date
    return_code: str | None = None


class Price(Model):  # 6. Reference and market data
    date: date
    symbol: str
    close_px: Decimal


class SymbolRef(Model):
    symbol: str
    cik: str
    name: str


class CorporateAction(Model):
    action_id: str
    symbol: str
    type: Literal["split", "dividend"]
    ratio: Decimal | None = None  # split: new shares per old share
    rate: Decimal | None = None  # dividend: USD per share
    ex_date: date
    record_date: date
    pay_date: date


class FeeTier(Model):  # 7. Fee schedule (client agreement)
    tier: str
    per_trade: Decimal
    per_share: Decimal
    min_fee: Decimal
    max_fee: Decimal


class FeeSchedule(Model):
    version: int
    effective_from: date
    tiers: list[FeeTier]


class Account(Model):
    account_id: str
    name: str
    tier: str
    opened: date


class Person(Model):  # 8. People directory
    role: str
    name: str
    owns: str
    approval_limit: Decimal | None = None
    reply_sla_days: int = 1
    email: str


# ---------------------------------------------------------------- domain

BreakType = Literal["B1", "B2", "B3", "B4", "H1", "H2", "N1", "N2", "S1", "S2", "UNKNOWN"]
CaseStatus = Literal[
    "new", "investigating", "waiting", "awaiting_approval", "completed", "auto_cleared", "needs_human"
]


class Break(Model):
    break_id: str
    detection_key: str
    break_key: str  # which sources disagree on which field, e.g. ledger-custodian:trade_qty
    level: Literal["transaction", "position", "cash"]
    account_id: str | None
    symbol: str | None
    ref: str | None
    sources: list[str]
    values: dict[str, str]  # per-source value, as strings of Decimal
    amount_usd: Decimal
    detected_on: date
    status: Literal["open", "resolved", "auto_cleared"]
    harmless: bool = False
    reason: str | None = None


class Action(Model):
    type: Literal[
        "journal_entry", "source_correction_request", "transfer", "request_info", "notify", "hold", "escalate"
    ]
    template: str | None = None  # journal_entry: reverse | adjust | dividend | split_adjust | reverse_unfilled | ...
    to_role: str | None = None


class Proposal(Model):
    proposal_id: str
    case_id: str
    action: Action
    postings: list[Posting] = Field(default_factory=list)
    transfer: dict | None = None
    amount_usd: Decimal
    calculator: str  # the deterministic calculator that produced every number (R11)
    calc_inputs: dict
    references: list[str] = Field(default_factory=list)
    memo: str
    prepared_by: str


class AnswerKeyRow(Model):
    break_id: str
    type: BreakType
    injected_on: date
    affected: list[dict]  # [{"source": ..., "id": ...}]
    expected_detection_keys: list[str]
    root_cause_source: str
    expected_fix: list[dict]  # [{"action": ..., "template": ..., "amounts": {...}}]
    expected_final_status: Literal["completed", "auto_cleared", "needs_human"]
    first_status: Literal["needs_human"] | None = None  # novel: stops for a human before the lesson exists
    note: str = ""
