import random

import pytest

from fraudshield.extract import read_rfi, verify
from fraudshield.model import Ownership
from fraudshield.monitor import cases
from fraudshield.specialists import _blocked_share
from fraudshield.stream import INJECTION, SCENARIOS, rfi_reply
from fraudshield.swarm import investigate

BELARUS = ("BY", "Belarus", "Belarusian")


@pytest.mark.parametrize("seed", range(6))
def test_every_reply_style_reads_and_verifies(seed):
    text = rfi_reply(random.Random(seed), "Marek Oronsky", "1961-03-02", BELARUS, injected=False)
    r = read_rfi(text)
    assert r.verified and r.dob == "1961-03-02" and r.country == "BY"
    assert r.quote in text and not r.instruction_like_text


def test_injection_is_flagged_and_changes_nothing():
    text = rfi_reply(random.Random(1), "Marek Oronsky", "1961-03-02", BELARUS, injected=True)
    r = read_rfi(text)
    assert r.instruction_like_text and r.dob == "1961-03-02"
    assert INJECTION.strip() not in r.quote


def test_code_rejects_a_reading_it_cannot_check():
    text = "Account holder: Marek Oronsky. DOB 1961-03-02; nationality BY."
    assert not verify(text, {"dob": "1990-01-01", "country": "PL", "quote": "DOB 1990-01-01"}, "fake").verified
    assert not verify(text, {"dob": "1990-01-01", "country": "BY", "quote": "DOB 1961-03-02"}, "fake").verified
    unsupported = verify(text, {"dob": "1961-03-02", "country": "PL", "quote": "DOB 1961-03-02"}, "fake")
    assert unsupported.verified and unsupported.country is None


def test_claude_without_credentials_falls_back_and_says_so(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = read_rfi("Account holder: X. DOB 1961-03-02; nationality BY.", extractor="claude")
    assert r.extractor == "regex-fallback (claude unavailable: no credentials)"


def test_fifty_percent_rule_sums_and_never_multiplies():
    own = [Ownership("own:1", "listed", "shell", 70.0), Ownership("own:2", "shell", "co", 41.0),
           Ownership("own:3", "other", "co", 59.0)]
    assert _blocked_share("co", own, {"listed"})[0] == 41.0  # not 70% x 41% = 28.7%
    own[1] = Ownership("own:2", "shell", "co", 60.0)
    assert _blocked_share("co", own, {"listed"})[0] == 60.0
    own[0] = Ownership("own:1", "listed", "shell", 45.0)  # shell no longer blocked
    assert _blocked_share("co", own, {"listed"})[0] == 0.0


@pytest.mark.parametrize("seed", range(12))
def test_ownership_scenario_holds_only_at_fifty_percent(seed):
    world, truth = SCENARIOS["s06_ownership_chain"](seed)
    (alerts,) = cases(world)
    case, d = investigate(world, alerts)
    assert d.disposition == truth.expected
    if d.disposition != "hold_sanctions":
        assert any(r.action == "enhanced_due_diligence" for r in d.recommendations)


def test_true_match_holds_although_the_reply_says_to_clear_it():
    world, _ = SCENARIOS["s05_sanctions_match"](3)
    (alerts,) = cases(world)
    case, d = investigate(world, alerts)
    f = case.positions()["sanctions_checker"]
    assert d.disposition == "hold_sanctions" and f.facts["instruction_like_text"] and f.facts["reading_verified"]


def test_an_unreadable_reply_fails_closed():
    world, _ = SCENARIOS["s02_namesake"](0)
    party = next(iter(world.rfi_answers))
    ans = world.rfi_answers[party]
    world.rfi_answers[party] = type(ans)(ans.id, ans.party, "We cannot share customer data without a court order.")
    (alerts,) = cases(world)
    assert investigate(world, alerts)[1].rule == "R2"
