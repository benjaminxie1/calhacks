"""Test fixtures. Tests use their own Postgres databases, never the demo database.

- `db`: a fresh, empty schema per test (unit tests).
- `quarter`: one full scripted replay of the season, shared by the tests that read its results.
"""
import os

os.environ.setdefault("BP_LLM", "offline")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from bp import calendar, config  # noqa: E402
from bp.sources.store import SourceStore, get_store  # noqa: E402

BASE = config.DATABASE_URL.rsplit("/", 1)[0]
CLAUDE = os.environ.get("BP_LLM") == "claude"
LLM_RUNS = 3 if CLAUDE else 1  # LLM-dependent tests run 3x at low effort and must pass 3/3


def _make_db(name: str):
    admin = create_engine(f"{BASE}/breakpoint", isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        if not c.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).scalar():
            c.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    return create_engine(f"{BASE}/{name}", future=True)


@pytest.fixture(scope="session")
def world_dir() -> Path:
    get_store()  # builds data/world/seed42 if missing
    return config.world_dir()


@pytest.fixture
def store(world_dir) -> SourceStore:
    """A private store (live injections mutate stores)."""
    return SourceStore(world_dir)


@pytest.fixture(scope="session")
def _unit_engine():
    return _make_db("breakpoint_test")


@pytest.fixture
def db(_unit_engine):
    from bp.db import reset
    from bp.ledger import service as ledger

    reset(_unit_engine)
    with _unit_engine.begin() as c:
        ledger.ensure_periods(c)
    return _unit_engine


@pytest.fixture(scope="session")
def quarter(world_dir):
    """Full scripted replay of the season (Feb 2 - May 29) with agents, recording the trial balance after every day."""
    from bp.engine.replay import Replay
    from bp.ledger import service as ledger

    eng = _make_db("breakpoint_quarter")
    rp = Replay(eng=eng, store=SourceStore(world_dir), controller_mode="scripted", teach_mode="scripted")
    rp.init(fresh=True)
    days, tb = [], {}

    def on_day(r):
        days.append(r)
        with eng.connect() as c:
            tb[r["date"]] = {a: v for a, v in ledger.trial_balance(c).items() if v != 0}

    rp.run(on_day=on_day)
    return {"eng": eng, "replay": rp, "days": days, "trial_balance": tb}


def run_until(rp, end):
    return rp.run(calendar.START, end)
