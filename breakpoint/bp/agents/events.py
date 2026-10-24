"""Append-only event log (R24): every state change records actor, time, case, inputs,
evidence references and a short reason."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from bp.db import events
from bp.money import s as dstr


def jsonable(x):
    if isinstance(x, Decimal):
        return dstr(x)
    if isinstance(x, date):
        return x.isoformat()
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [jsonable(v) for v in x]
    if hasattr(x, "model_dump"):
        return x.model_dump(mode="json")
    return x


def emit(conn, actor: str, action: str, reason: str, *, case_id: str | None = None, inputs=None,
         evidence=None, sim_date: date | None = None) -> None:
    conn.execute(events.insert().values(
        sim_date=sim_date, actor=actor, action=action, case_id=case_id, reason=reason,
        inputs=jsonable(inputs or {}), evidence=jsonable(evidence or [])))
