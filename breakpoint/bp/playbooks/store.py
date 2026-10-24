"""Playbook library: versioned, with status and per-branch stats."""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from bp.agents.events import emit
from bp.db import playbooks
from bp.playbooks.schema import Playbook


def save(conn, pb: Playbook, created_by: str, d: date | None, note: str = "") -> Playbook:
    conn.execute(playbooks.insert().values(id=pb.id, version=pb.version, status=pb.status, key_name=pb.key.name(),
                                           body=pb.dump(), created_on=d, created_by=created_by, note=note))
    emit(conn, created_by, "playbook_saved", note or f"{pb.id} v{pb.version} saved as {pb.status}",
         inputs={"playbook": pb.id, "version": pb.version, "status": pb.status}, sim_date=d)
    return pb


def get(conn, pid: str, version: int) -> Playbook | None:
    r = conn.execute(select(playbooks.c.body).where(playbooks.c.id == pid, playbooks.c.version == version)).scalar()
    return Playbook.model_validate(r) if r else None


def latest_version(conn, pid: str) -> int:
    return conn.execute(select(func.max(playbooks.c.version)).where(playbooks.c.id == pid)).scalar() or 0


def for_key(conn, key_name: str, status: str | None = "active") -> Playbook | None:
    q = select(playbooks.c.body).where(playbooks.c.key_name == key_name)
    if status:
        q = q.where(playbooks.c.status == status)
    r = conn.execute(q.order_by(playbooks.c.version.desc())).scalar()
    return Playbook.model_validate(r) if r else None


def all_rows(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(select(playbooks).order_by(playbooks.c.id, playbooks.c.version)).mappings()]


def set_status(conn, pid: str, version: int, status: str, actor: str, reason: str, d: date | None,
               approved_by: str | None = None) -> None:
    pb = get(conn, pid, version)
    body = pb.model_copy(update={"status": status}).dump()
    vals = {"status": status, "body": body}
    if approved_by:
        vals["approved_by"] = approved_by
    if status == "active":  # one active version per playbook
        for r in conn.execute(select(playbooks.c.version).where(playbooks.c.id == pid, playbooks.c.status == "active",
                                                                 playbooks.c.version != version)):
            old = get(conn, pid, r.version)
            conn.execute(playbooks.update().where(playbooks.c.id == pid, playbooks.c.version == r.version)
                         .values(status="retired", body=old.model_copy(update={"status": "retired"}).dump()))
    conn.execute(playbooks.update().where(playbooks.c.id == pid, playbooks.c.version == version).values(**vals))
    emit(conn, actor, f"playbook_{status}", reason, inputs={"playbook": pid, "version": version}, sim_date=d)


def bump_stats(conn, pid: str, version: int, branch: str, succeeded: bool) -> None:
    pb = get(conn, pid, version)
    body = pb.dump()
    st = body.setdefault("stats", {}).setdefault(branch, {"used": 0, "succeeded": 0})
    st["used"] += 1
    st["succeeded"] += int(succeeded)
    conn.execute(playbooks.update().where(playbooks.c.id == pid, playbooks.c.version == version).values(body=body))
