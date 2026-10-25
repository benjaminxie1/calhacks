"""Contracts for every source and every domain object.

Money and percentages are Decimal everywhere (R6). Each source model mirrors one
system's view of the world; none of them is the truth.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- sources (1-8)

class Clause(Model):
    section: str
    quote: str  # verbatim from the lease text


class Landlord(Model):  # 1. reference: landlords and their property managers
    landlord_id: str
    name: str
    contact: str
    email: str
    reply_sla_days: int = 2


class Center(Model):  # 1. reference: shopping centers
    center_id: str
    name: str
    address: str
    city: str
    county: str
    apn: str  # assessor's parcel number
    gla_sf: Decimal  # gross leasable area
    landlord_id: str


class Lease(Model):  # 2. lease administration system: the abstract of each store's lease
    lease_id: str
    store: str
    tenant_entity: str
    center_id: str
    landlord_id: str
    suite: str
    premises_sf: Decimal
    gla_sf: Decimal
    share_pct: Decimal  # tenant's share, percent
    cap_pct: Decimal | None  # max yearly increase in controllable expenses, percent
    admin_fee_pct: Decimal | None
    admin_fee_base_excludes: list[str]  # categories the fee may not be charged on
    capital_excluded: bool
    taxes_actual_paid: bool
    gross_up_pct: Decimal | None  # occupancy the lease lets the landlord gross variable costs up to; None = no clause
    audit_window_days: int
    monthly_estimate_2025: Decimal
    monthly_estimate_2026: Decimal
    clauses: dict[str, Clause]


Category = Literal["controllable", "utilities", "insurance", "taxes", "repairs", "admin_fee", "gross_up",
                   "cap_adjustment"]


class StatementLine(Model):
    line_id: str
    description: str
    category: Category
    amount: Decimal  # center level, as the landlord states it
    tenant_amount: Decimal  # the tenant's share the landlord printed for this line


class Statement(Model):  # 3. landlord statements (the annual CAM reconciliation, as received)
    statement_id: str
    lease_id: str
    landlord_id: str
    year: int
    statement_date: date
    received_on: date
    share_billed_pct: Decimal
    occupied_sf: Decimal  # what the landlord reports as leased and occupied
    occupancy_pct: Decimal
    prior_year_controllable: Decimal
    lines: list[StatementLine]
    tenant_total: Decimal  # sum of tenant_amount
    estimates_credited: Decimal
    balance_due: Decimal  # tenant_total - estimates_credited (negative = credit to the tenant)


class EstimatePayment(Model):  # 4. accounts payable: monthly CAM estimate payments
    payment_id: str
    lease_id: str
    period: str  # YYYY-MM the estimate covers
    amount: Decimal
    paid_on: date


class BankItem(Model):  # 5. bank: what actually cleared
    payment_id: str
    lease_id: str
    amount: Decimal
    status: Literal["cleared", "returned"]
    status_date: date


class TaxRecord(Model):  # 6. county property tax records (Apify Actor output shape)
    apn: str
    county: str
    tax_year: str
    total_tax: Decimal
    installments: list[dict]
    source: Literal["apify", "cache"] = "cache"


class Posting(Model):
    account: str
    asset: str
    amount: Decimal


class LedgerTxn(Model):  # 7. booking feed (what AP codes and sends to the general ledger)
    txn_id: str
    date: date
    settle_date: date
    kind: str
    postings: list[Posting]
    source_ref: str
    reason: str
    actor: str = "ap-system"


class Person(Model):  # 8. people directory
    role: str
    name: str
    owns: str
    approval_limit: Decimal | None = None
    reply_sla_days: int = 1
    email: str


# ---------------------------------------------------------------- domain

BreakType = Literal["share", "admin_fee", "cap", "capital", "tax", "estimates", "rounding", "gross_up", "UNKNOWN"]
CaseStatus = Literal[
    "new", "investigating", "waiting", "awaiting_approval", "completed", "auto_cleared", "needs_human"
]


class Break(Model):
    break_id: str
    detection_key: str
    break_key: str  # which sources disagree on which field, e.g. statement-lease:tenant_share
    level: Literal["statement"]
    account_id: str | None  # the lease
    symbol: str | None  # the landlord
    ref: str | None  # the statement
    sources: list[str]
    values: dict[str, str]
    amount_usd: Decimal
    detected_on: date
    status: Literal["open", "resolved", "auto_cleared"]
    harmless: bool = False
    reason: str | None = None


class Action(Model):
    type: Literal[
        "journal_entry", "source_correction_request", "transfer", "request_info", "notify", "hold", "escalate"
    ]
    template: str | None = None  # journal_entry: the calculator that produces its amounts
    to_role: str | None = None


class Proposal(Model):
    proposal_id: str
    case_id: str
    action_name: str  # the library action this proposal carries out
    action: Action
    postings: list[Posting] = Field(default_factory=list)
    transfer: dict | None = None
    amount_usd: Decimal
    calculator: str  # the deterministic calculator that produced every number (R11)
    calc_inputs: dict
    references: list[str] = Field(default_factory=list)  # original entries this corrects
    source_ref: str  # the finding the fix belongs to (its detection key)
    playbook: dict | None = None  # {"id", "version", "branch"} or None for a taught one-off fix
    memo: str
    prepared_by: str


class AnswerKeyRow(Model):
    break_id: str
    type: BreakType
    injected_on: date
    affected: list[dict]  # [{"source": ..., "id": ...}]
    expected_detection_keys: list[str]
    root_cause_source: str
    expected_fix: list[dict]  # [{"action": ..., "amounts": {...}}]
    expected_final_status: Literal["completed", "auto_cleared", "needs_human"]
    first_status: Literal["needs_human"] | None = None  # novel: stops for a human before the lesson exists
    note: str = ""

