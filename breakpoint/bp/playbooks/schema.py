"""Playbook schema (R19): one playbook per break key, ordered branches, modifiers,
versions, per-branch provenance and stats."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bp.playbooks import conditions
from bp.playbooks.library import ACTIONS


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Key(Strict):
    sources: list[str]
    field: str

    def name(self) -> str:
        return f"{'-'.join(self.sources)}:{self.field}"


class Provenance(Strict):
    taught_by: str
    from_case: str
    approved_by: str | None = None


class Branch(Strict):
    id: str
    when: str
    trust: str
    actions: list[str] = Field(min_length=1)
    provenance: Provenance
    test: dict | None = None  # facts from the teaching case; must match this branch

    @field_validator("actions")
    @classmethod
    def _known(cls, v):
        bad = [a for a in v if a not in ACTIONS]
        if bad:
            raise ValueError(f"unknown actions {bad}; the action library is closed: {sorted(ACTIONS)}")
        return v


class Modifier(Strict):
    when: str
    skip: list[str]


class Approval(Strict):
    auto_under_usd: int = 500
    otherwise: Literal["controller"] = "controller"


class BranchStats(Strict):
    used: int = 0
    succeeded: int = 0


class Playbook(Strict):
    id: str
    version: int = Field(ge=1)
    status: Literal["draft", "active", "flagged", "retired"]
    key: Key
    context: list[str] = Field(min_length=1)
    branches: list[Branch]
    else_: Literal["needs_human"] = Field("needs_human", alias="else")
    modifiers: list[Modifier] = Field(default_factory=list)
    approval: Approval = Field(default_factory=Approval)
    verify: str = "rerun_recon(affected_keys) == 0 breaks"
    stats: dict[str, BranchStats] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _conditions_read_only_context(self):
        ctx = set(self.context)
        seen = set()
        for b in self.branches:
            if b.id in seen:
                raise ValueError(f"duplicate branch id {b.id!r}")
            seen.add(b.id)
            try:
                used = conditions.names(b.when)
            except conditions.ConditionError as e:
                raise ValueError(f"branch {b.id}: {e}") from None
            extra = used - ctx
            if extra:
                raise ValueError(f"branch {b.id} reads {sorted(extra)} which are not in context (R19)")
        for m in self.modifiers:
            extra = conditions.names(m.when) - ctx
            if extra:
                raise ValueError(f"modifier reads {sorted(extra)} which are not in context")
        return self

    def dump(self) -> dict:
        return self.model_dump(mode="json", by_alias=True)
