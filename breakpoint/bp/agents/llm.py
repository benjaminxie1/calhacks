"""One thin wrapper around the model, so the agent framework stays swappable.

Every agent decision goes through `decide()`. With ANTHROPIC_API_KEY set (and
BP_LLM not "offline"), it asks Claude for a structured answer validated against a
Pydantic schema. Without a key, it runs the agent's deterministic offline policy.
Either way the result is the same type, and the backend used is logged.

The model chooses, explains and drafts. It never produces a number that gets posted.
"""
from __future__ import annotations

import json
import os
from typing import Callable, TypeVar

from pydantic import BaseModel

from bp import config

T = TypeVar("T", bound=BaseModel)

SYSTEM = (
    "You are one agent on Breakpoint, the reconciliation team at Meridian Securities, a retail broker-dealer. "
    "You work only from the evidence given. You never invent or compute amounts: every number that gets posted "
    "comes from deterministic calculators, and you may only cite them. When the evidence does not support a "
    "conclusion, say so plainly; asking a human is a correct outcome."
)


def backend() -> str:
    if os.environ.get("BP_LLM", "").lower() == "offline":
        return "offline"
    if os.environ.get("BP_LLM", "").lower() == "claude" or os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    return "offline"


_client = None


def _claude():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic()
    return _client


def _jsonable(x):
    from bp.agents.events import jsonable

    return jsonable(x)


def decide(agent: str, task: str, payload: dict, schema: type[T], offline: Callable[[], T],
           effort: str = "low") -> tuple[T, str]:
    """Return (answer, backend). Falls back to the offline policy if the model call fails."""
    if backend() == "offline":
        return offline(), "offline"
    try:
        import anthropic

        resp = _claude().messages.parse(
            model=config.MODEL,
            max_tokens=4000,
            system=SYSTEM + f"\n\nYou are the {agent}.",
            output_config={"effort": effort},
            messages=[{"role": "user", "content": f"{task}\n\nEvidence (JSON):\n"
                       + json.dumps(_jsonable(payload), indent=1, sort_keys=True)}],
            output_format=schema,
        )
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            return offline(), "offline(fallback: refusal)"
        return resp.parsed_output, "claude"
    except anthropic.APIConnectionError:
        return offline(), "offline(fallback: connection)"
    except anthropic.APIStatusError as e:
        return offline(), f"offline(fallback: {e.status_code})"
