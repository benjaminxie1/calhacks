import importlib

import yaml

from proven import config
from proven.ap.decisions import MatchDecision


def test_modules_import():
    for mod in ["proven.sim.plant", "proven.dod.verifier", "proven.ap.matcher", "proven.bench"]:
        importlib.import_module(mod)


def test_rate_card_loads():
    card = yaml.safe_load((config.LEDGER / "rate_card.yaml").read_text())
    assert card["work_orders"]["replace_fan_filter"]["usd"] == 1800


def test_decision_schema_requires_reason():
    d = MatchDecision(invoice_id="INV-1", decision="HOLD", amount_usd=1800, reasons=["fan_rpm 0 after hold"],
                      release_condition="fan_rpm > 800 and heatsink within model for 30 min", requires_human=True)
    assert d.decision == "HOLD"
