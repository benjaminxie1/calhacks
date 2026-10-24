"""Append-only record of what each agent saw and did. This is the "clean record
of its work" a reviewer reads after the fact, and what the dispute room streams."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable


class Trail:
    def __init__(self, path: Path | None = None, sink: Callable[[dict[str, Any]], None] | None = None):
        self.events: list[dict[str, Any]] = []
        self.path = path
        self.sink = sink
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")

    def log(self, agent: str, action: str, **detail: Any) -> None:
        event = {"t": round(time.time(), 3), "agent": agent, "action": action, **detail}
        self.events.append(event)
        if self.path:
            with self.path.open("a") as f:
                f.write(json.dumps(event, default=str) + "\n")
        if self.sink:
            self.sink(event)
