"""LangSmith 추적 켜기/끄기 테스트 (#154). 실제로 LangSmith에 보내지 않는다.

    uv run python -m pytest -q tests/test_tracing.py
"""

from contextlib import contextmanager

import anthropic
import openai
import pytest

from negopilot_agent import tracing
from negopilot_agent.decomposition.backends import AnthropicBackend, OpenAIBackend
from negopilot_agent.routing import Router

from tests.test_routing import FakeCaller, fixture


def turn_on(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-not-real")


@pytest.mark.parametrize("flag, key, on", [
    ("false", "k", False), ("true", "", False), ("true", "k", True), ("TRUE", "k", True), ("1", "k", True),
])
def test_enabled_needs_flag_and_key(monkeypatch, flag, key, on):
    monkeypatch.setenv("LANGSMITH_TRACING", flag)
    monkeypatch.setenv("LANGSMITH_API_KEY", key)
    assert tracing.enabled() is on


def test_clients_are_untouched_when_tracing_is_off():
    client = openai.AsyncOpenAI(api_key="test")
    create = client.chat.completions.create
    backend = OpenAIBackend(client, "gpt-4.1-mini", max_tokens=100)
    assert backend.client is client and backend.client.chat.completions.create == create


def test_llm_clients_are_wrapped_when_tracing_is_on(monkeypatch):
    turn_on(monkeypatch)
    oa = openai.AsyncOpenAI(api_key="test")
    create = oa.chat.completions.create
    assert OpenAIBackend(oa, "gpt-4.1-mini", 100).client.chat.completions.create != create
    an = anthropic.AsyncAnthropic(api_key="test")
    messages = an.messages.create
    assert AnthropicBackend(an, "claude", "low", 100, False).client.messages.create != messages


async def test_tool_calls_are_not_traced_when_off(monkeypatch):
    import langsmith

    def fail(*args, **kwargs):
        raise AssertionError("추적이 꺼져 있으면 trace를 만들지 않는다")
    monkeypatch.setattr(langsmith, "trace", fail)
    offer = (await Router(FakeCaller()).route(fixture("05_offer_check"))).outcomes[0]
    assert offer.status == "ok"


async def test_each_mcp_tool_call_becomes_a_tool_run(monkeypatch):
    import langsmith

    runs = []

    class Run:
        def __init__(self, kwargs):
            self.kwargs, self.outputs = kwargs, None

        def end(self, outputs):
            self.outputs = outputs

    @contextmanager
    def fake_trace(**kwargs):
        run = Run(kwargs)
        runs.append(run)
        yield run

    turn_on(monkeypatch)
    monkeypatch.setattr(langsmith, "trace", fake_trace)
    caller = FakeCaller({"get_article": RuntimeError("law-mcp 꺼짐")})
    offer = (await Router(caller).route(fixture("05_offer_check"))).outcomes[0]
    assert [r.kwargs["name"] for r in runs] == ["business-mcp.check_offer", "law-mcp.get_article", "law-mcp.get_article"]
    assert all(r.kwargs["run_type"] == "tool" for r in runs)
    assert runs[0].kwargs["inputs"]["supplier"] == "SUP-0003"
    assert runs[0].outputs["result"]["verdict"] == "위반 소지 있음"
    assert runs[1].outputs is None                      # 실패한 호출은 결과 없이 오류로 남는다
    assert offer.status == "ok"                         # 추적이 도구 결과 처리를 바꾸지 않는다


def test_run_config_carries_request_id_and_mode():
    config = tracing.run_config("3f1c2a7e-9b4d-4c1e-8a2f-5d6e7f8a9b01", "live")
    assert config["run_name"] == "negopilot.query"
    assert config["metadata"] == {"request_id": "3f1c2a7e-9b4d-4c1e-8a2f-5d6e7f8a9b01", "mode": "live"}
