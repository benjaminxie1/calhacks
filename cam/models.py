"""Shared types. Every lease term carries the verbatim clause it came from;
every finding carries the dollars code computed and the evidence it rests on."""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Clause(BaseModel):
    section: str = Field(description="Section number as written in the lease, e.g. '5.4'")
    quote: str = Field(description="Verbatim text copied from the lease that states this term")


class LeaseTerms(BaseModel):
    """What the lease parser extracts. Numbers are what the lease says, not what the bill says."""
    premises_sf: float
    center_gla_sf: float
    tenant_share_pct: Optional[float] = Field(description="Share stated in the lease, if any, in percent")
    share_clause: Clause
    controllable_cap_pct: Optional[float] = Field(description="Max yearly increase in controllable expenses, percent; null if no cap")
    cap_clause: Optional[Clause]
    controllable_excludes: list[str] = Field(description="Expense kinds the lease says are NOT controllable, e.g. insurance, taxes, utilities")
    capital_excluded: bool
    capital_exclusion_clause: Optional[Clause]
    capital_savings_exception: bool = Field(description="True if capital items that reduce operating expenses may be included")
    admin_fee_max_pct: Optional[float]
    admin_fee_base_excludes: list[str] = Field(description="What the fee may NOT be charged on, e.g. insurance, taxes, capital")
    admin_fee_clause: Optional[Clause]
    taxes_actual_paid: bool = Field(description="True if tenant pays only taxes actually levied and paid")
    tax_clause: Optional[Clause]
    audit_window_days: Optional[int]
    audit_clause: Optional[Clause]


class Category(str, Enum):
    controllable = "controllable"
    utilities = "utilities"
    insurance = "insurance"
    taxes = "taxes"
    repairs = "repairs"
    admin_fee = "admin_fee"


class BillLine(BaseModel):
    id: str
    description: str
    amount: float
    category: Category


class Statement(BaseModel):
    landlord: str
    tenant: str
    year: int
    statement_date: str
    tenant_share_billed_pct: float
    estimates_paid: float
    prior_year_controllable_total: float
    lines: list[BillLine]


class LineClass(BaseModel):
    """How a line is treated under the lease, decided by the classifier."""
    line_id: str
    treatment: Literal["include", "exclude_capital", "ambiguous"]
    reason: str
    clause: Optional[Clause] = None


class TaxRecord(BaseModel):
    source: Literal["apify", "cache"]
    apn: str
    county: str
    tax_year: str
    total_tax_billed: float
    url: Optional[str] = None
    fetched_at: Optional[str] = None


class Finding(BaseModel):
    rule: str
    title: str
    line_ids: list[str] = []
    tenant_overcharge: float = Field(description="Dollars at the tenant's correct share, computed in code")
    confidence: Literal["high", "medium", "low"]
    clause: Optional[Clause] = None
    evidence: list[str] = []
    explanation: str
    needs_owner: bool = False
    owner_question: Optional[str] = None


class AuditResult(BaseModel):
    billed_tenant_total: float
    correct_tenant_total: float
    estimates_paid: float
    landlord_says_due: float
    actually_due: float
    findings: list[Finding]
    pending_lines: list[str] = Field(description="Lines left out of the corrected total until the owner decides")
