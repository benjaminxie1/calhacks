"""Environment and constants. Keys come from .env or the shell; never from the repo."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "ledger"
load_dotenv(ROOT / ".env")

VERIFIER_MODEL = os.getenv("VERIFIER_MODEL", "claude-opus-5-5")
APPROVAL_THRESHOLD_USD = float(os.getenv("APPROVAL_THRESHOLD_USD", "2500"))
HOLD_MINUTES = int(os.getenv("HOLD_MINUTES", "30"))

# What Droid is allowed to write. Everything else in the ledger is read-only to it.
DROID_WRITABLE = ("ledger/decisions/", "ledger/journal.csv")
