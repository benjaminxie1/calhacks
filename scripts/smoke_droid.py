"""Smoke test 2: one Droid SDK run inside the ledger directory, the way the matcher will run it.

Needs the `droid` CLI on PATH and either a logged-in CLI (`droid` once, sign in) or FACTORY_API_KEY.
Exits 0 on a real reply, 2 if droid/auth is missing, 1 otherwise.
"""
from __future__ import annotations

import asyncio
import shutil
import sys

from proven.config import LEDGER  # loads .env


async def main() -> int:
    if not shutil.which("droid"):
        print("MISSING: droid CLI not on PATH (brew install --cask droid)")
        return 2
    from droid_sdk import run

    try:
        result = await run(
            "You are being smoke-tested. Do not read or write any file. Reply with the single word READY.",
            cwd=str(LEDGER),
            timeout=120,
        )
    except Exception as e:  # SDK surface is young; show the raw failure
        msg = str(e)
        hint = " (sign in: run `droid` once, or set FACTORY_API_KEY)" if "auth" in msg.lower() or "login" in msg.lower() or "key" in msg.lower() else ""
        print(f"DROID ERROR: {e.__class__.__name__}: {msg}{hint}")
        return 2
    if not result.success:
        print(f"DROID FAILED: subtype={result.subtype} error={getattr(result, 'error', None)}")
        return 1
    print(f"session={result.session_id} reply={result.text.strip()!r} duration={result.duration}")
    return 0 if "READY" in (result.text or "").upper() else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
