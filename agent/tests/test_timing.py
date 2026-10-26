"""단계별 시간 측정 테스트: 노드·도구·LLM 호출이 기록되고, collect() 밖에서는 아무것도 남지 않는다.

    uv run python -m pytest -q tests/test_timing.py
"""

import json

import httpx2
import openai
import pytest

from negopilot_agent import timing
from negopilot_agent.decomposition.backends import OpenAIBackend

from tests.test_graph import make_app


async def test_graph_records_nodes_and_tool_calls():
    app, query, _, caller = make_app("03_rank_then_law")
    with timing.collect() as t:
        await app.ainvoke({"query": query})

    nodes = [s.name for s in t.spans if s.kind == "node"]
    assert nodes == ["decompose_query", "route", "generate", "review"]   # 보강할 거리가 없으면 검토 후 끝난다
    tools = [s for s in t.spans if s.kind == "tool"]
    assert len(tools) == len(caller.calls) > 0
    # 도구 호출은 route 노드 안에서 일어난 것으로 기록된다
    assert {s.stage for s in tools} == {"route"}
    assert all(s.name.startswith(("business-mcp.", "law-mcp.")) for s in tools)

    summary = t.summary()
    assert summary["tool_calls"] == len(tools)
    assert summary["total"] >= summary["route"] >= 0
    assert "route" in t.report()


async def test_nothing_is_recorded_outside_collect():
    app, query, _, _ = make_app("05_offer_check")
    with timing.collect() as t:
        pass
    await app.ainvoke({"query": query})
    assert t.spans == []


async def test_openai_backend_records_tokens():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={
            "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "gpt-4.1-mini",
            "choices": [{"index": 0, "finish_reason": "stop", "logprobs": None,
                         "message": {"role": "assistant", "content": json.dumps({"status": "failed"}),
                                     "refusal": None}}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 80, "total_tokens": 1280,
                      "prompt_tokens_details": {"cached_tokens": 1024}}})

    client = openai.AsyncOpenAI(api_key="test-key", max_retries=0,
                                http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    backend = OpenAIBackend(client, "gpt-4.1-mini", max_tokens=100)
    with timing.collect() as t, timing.span("node", "decompose_query"):
        await backend.generate("system", "user")

    llm = [s for s in t.spans if s.kind == "llm"]
    assert len(llm) == 1 and llm[0].stage == "decompose_query"
    assert llm[0].meta == {"input_tokens": 1200, "output_tokens": 80, "cached_tokens": 1024}
    assert t.summary()["cached_tokens"] == 1024


def test_cost_uses_cached_price_and_ignores_unknown_models():
    """비용 = (입력-캐시)×입력 단가 + 캐시×캐시 단가 + 출력×출력 단가. 단가를 모르는 모델은 빼고 센다 (#108)."""
    from negopilot_agent.timing import Span, Timings, cost_usd
    known = Span("llm", "gpt-4.1-mini-2025-04-14", "generate", 0, 1,
                 {"input_tokens": 10_000, "cached_tokens": 8_000, "output_tokens": 500})
    unknown = Span("llm", "claude-opus-5-5", "decompose_query", 0, 1, {"input_tokens": 1000, "output_tokens": 100})
    assert cost_usd(known) == pytest.approx((2_000 * 0.40 + 8_000 * 0.10 + 500 * 1.60) / 1_000_000)
    assert cost_usd(unknown) is None
    summary = Timings(spans=[known, unknown]).summary()
    assert summary["cost_usd"] == pytest.approx(0.0024)
    assert summary["cost_unknown_calls"] == 1
