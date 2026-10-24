"""Decision schema. Droid's structured output must validate against this."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Decision = Literal["APPROVE", "HOLD", "DISPUTE", "ESCALATE"]


class MatchDecision(BaseModel):
    invoice_id: str
    work_order_id: str | None = None
    decision: Decision
    amount_usd: float
    reasons: list[str] = Field(min_length=1)
    release_condition: str | None = None   # required when decision == HOLD
    evidence_refs: list[str] = []
    requires_human: bool
    droid_session_id: str | None = None
