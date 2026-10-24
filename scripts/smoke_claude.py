"""Smoke test 1: one Claude call through the official SDK, the way the verifier will call it.

Credentials: ANTHROPIC_API_KEY in .env or the shell (or an `ant auth login` profile).
Exits 0 on a real reply, 2 on a credential problem, 1 on anything else.
"""
from __future__ import annotations

import sys

import anthropic

from proven.config import VERIFIER_MODEL  # loads .env

CHECK = {"kind": "telemetry", "op": "threshold", "asset": "INV-1", "field": "fan_rpm", "min": 800}
OBSERVED = {"asset": "INV-1", "fan_rpm": 0, "heatsink_c": 71.2, "last_update_ts": "2026-10-24T14:32:00"}


def main() -> int:
    client = anthropic.Anthropic()
    try:
        resp = client.beta.messages.create(
            model=VERIFIER_MODEL,
            max_tokens=256,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system="You are an independent verifier. Reply with exactly one word: PASS or FAIL.",
            messages=[{"role": "user", "content": f"Check: {CHECK}\nObserved: {OBSERVED}\nDoes the check pass?"}],
        )
    except (anthropic.AuthenticationError, TypeError) as e:
        # anthropic 2.x raises TypeError at request build time when no credential source resolves
        print(f"CREDENTIALS: {e.__class__.__name__}: set ANTHROPIC_API_KEY in .env (see .env.example)")
        return 2
    except anthropic.APIStatusError as e:
        print(f"API ERROR {e.status_code}: {e.message}")
        return 1
    except anthropic.APIConnectionError as e:
        print(f"CONNECTION: {e}")
        return 1
    if resp.stop_reason == "refusal":
        print(f"REFUSAL: {resp.stop_details}")
        return 1
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    print(f"model={resp.model} stop={resp.stop_reason} reply={text!r} in={resp.usage.input_tokens} out={resp.usage.output_tokens}")
    return 0 if text.upper().startswith("FAIL") else 1


if __name__ == "__main__":
    sys.exit(main())
