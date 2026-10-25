"""The Claude path, against a mocked API: every failure mode falls back to the offline policy (or raises under
BP_LLM_STRICT, which make test-llm sets), and BP_LLM_AGENTS limits which agents call the model."""
import json

import anthropic
import pytest

try:
    import httpx2 as httpx  # anthropic >= 1.x
except ImportError:  # pragma: no cover
    import httpx

from bp.agents import llm
from bp.agents.core import Review


def _reply(status=200, text=None, stop="end_turn"):
    def handler(req):
        if status != 200:
            return httpx.Response(status, json={"type": "error", "error": {"type": "api_error", "message": "x"}})
        content = [] if text is None else [{"type": "text", "text": text}]
        return httpx.Response(200, json={"id": "msg_t", "type": "message", "role": "assistant", "model": "m",
                                         "stop_reason": stop, "stop_sequence": None, "content": content,
                                         "usage": {"input_tokens": 1, "output_tokens": 1}})
    return anthropic.Anthropic(api_key="test", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))


OFFLINE = Review(approve=False, reason="offline policy")


@pytest.fixture
def claude(monkeypatch):
    monkeypatch.setenv("BP_LLM", "claude")
    monkeypatch.delenv("BP_LLM_STRICT", raising=False)
    monkeypatch.delenv("BP_LLM_AGENTS", raising=False)
    yield lambda client: monkeypatch.setattr(llm, "_client", client)
    llm._client = None


def _decide():
    return llm.decide("approver", "review", {"x": 1}, Review, lambda: OFFLINE)


def test_model_answer_is_used(claude):
    claude(_reply(text=json.dumps({"approve": True, "reason": "fits the branch"})))
    assert _decide() == (Review(approve=True, reason="fits the branch"), "claude")


@pytest.mark.parametrize("kw,why", [({"status": 500}, "500"), ({"status": 429}, "429"),
                                    ({"text": "not json"}, "invalid output"),
                                    ({"text": '{"approve": true'}, "invalid output"),
                                    ({"text": None, "stop": "refusal"}, "refusal")])
def test_failures_fall_back_to_offline(claude, kw, why):
    claude(_reply(**kw))
    ans, be = _decide()
    assert ans == OFFLINE and be == f"offline(fallback: {why})"


def test_strict_mode_raises_instead(claude, monkeypatch):
    claude(_reply(status=500))
    monkeypatch.setenv("BP_LLM_STRICT", "1")
    with pytest.raises(llm.ModelFallback):
        _decide()


def test_agent_allowlist(claude, monkeypatch):
    claude(_reply(text=json.dumps({"approve": True, "reason": "model"})))
    monkeypatch.setenv("BP_LLM_AGENTS", "investigator,outreach")
    assert _decide() == (OFFLINE, "offline")
    assert llm.backend("outreach") == "claude" and llm.backend("persona Dana Ortiz (ops_lead)") == "offline"
