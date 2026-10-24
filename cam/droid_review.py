"""Second reader: Factory's Droid checks the drafted letter against the
auditor's numbers and the lease before the owner sees it. It runs read-only
(`droid exec` with no --auto), so it can flag problems but never edit or send.

Needs FACTORY_API_KEY and the `droid` CLI (curl -fsSL https://app.factory.ai/cli | sh).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

PROMPT = """You are reviewing a CAM dispute letter before a small-business owner signs it.
Files in this directory: lease.md (the lease), result.json (the auditor's computed findings;
these numbers are authoritative), letter.txt (the draft).

Check, reading the files directly:
1. Every dollar figure in letter.txt appears in result.json (as an amount, a total, or a sum of findings).
2. Every lease section the letter cites exists in lease.md and says what the letter claims.
3. No finding with needs_owner=true is asserted in the letter as settled.
4. The tone is firm and factual, with no accusation of fraud.

Reply with only a JSON object: {"ok": true|false, "issues": ["..."]}"""


def available() -> bool:
    return bool(os.environ.get("FACTORY_API_KEY")) and shutil.which("droid") is not None


def review(run_dir: Path, lease_path: Path, timeout: int = 300) -> dict:
    shutil.copy(lease_path, run_dir / "lease.md")
    proc = subprocess.run(["droid", "exec", "-o", "json", "--cwd", str(run_dir), PROMPT],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"droid exec failed: {proc.stderr.strip()[:500]}")
    out = json.loads(proc.stdout)
    text = out.get("result", "") if isinstance(out, dict) else str(out)
    start, end = text.find("{"), text.rfind("}")
    verdict = json.loads(text[start:end + 1]) if start >= 0 else {"ok": False, "issues": [text[:500]]}
    verdict["session_id"] = out.get("session_id") if isinstance(out, dict) else None
    return verdict
