"""Dispute room client: mirrors one audit into our SpacetimeDB module
(`spacetime/`) so the tenant watches findings arrive live and answers the open
questions there. Talks to SpacetimeDB's HTTP API; does nothing unless
SPACETIME_DB is set.

    SPACETIME_HOST   default https://maincloud.spacetimedb.com
    SPACETIME_DB     database name, e.g. cam-dispute-room
    SPACETIME_TOKEN  from `spacetime login show --token` (a fresh identity is minted if unset)
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any


class Room:
    def __init__(self, db: str, host: str = "https://maincloud.spacetimedb.com", token: str | None = None):
        self.base = f"{host.rstrip('/')}/v1/database/{db}"
        self.host = host.rstrip("/")
        self.token = token or self._new_identity()

    @classmethod
    def from_env(cls) -> "Room | None":
        db = os.environ.get("SPACETIME_DB")
        if not db:
            return None
        return cls(db, os.environ.get("SPACETIME_HOST", "https://maincloud.spacetimedb.com"),
                   os.environ.get("SPACETIME_TOKEN") or None)

    def _post(self, url: str, body: bytes, content_type: str, auth: bool = True) -> bytes:
        headers = {"Content-Type": content_type}
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()

    def _new_identity(self) -> str:
        return json.loads(self._post(f"{self.host}/v1/identity", b"", "application/json", auth=False))["token"]

    def call(self, reducer: str, *args: Any) -> None:
        self._post(f"{self.base}/call/{reducer}", json.dumps(list(args)).encode(), "application/json")

    def sql(self, query: str) -> list[dict[str, Any]]:
        out = json.loads(self._post(f"{self.base}/sql", query.encode(), "text/plain"))
        rows: list[dict[str, Any]] = []
        for stmt in out:
            names = [e["name"]["some"] if isinstance(e["name"], dict) else e["name"]
                     for e in stmt["schema"]["elements"]]
            rows += [dict(zip(names, r)) for r in stmt["rows"]]
        return rows

    def rulings(self, audit_key: str) -> dict[str, str]:
        """Owner answers given in the room, as {rule: ruling}."""
        key = audit_key.replace("'", "''")
        rows = self.sql(f"SELECT rule, ruling FROM finding WHERE audit_key = '{key}'")
        out = {}
        for r in rows:
            ruling = r["ruling"]
            # SQL returns Option<String> as a sum: [0, value] for some, [1, []] for none
            if isinstance(ruling, list):
                ruling = ruling[1] if ruling[0] == 0 else None
            if ruling:
                out[r["rule"]] = ruling
        return out


def opt(v: str | None) -> dict:
    """SpacetimeDB's JSON encoding of Option<T>."""
    return {"some": v} if v is not None else {"none": []}
