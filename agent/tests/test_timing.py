"""단계별 시간 측정 테스트: 노드·도구·LLM 호출이 기록되고, collect() 밖에서는 아무것도 남지 않는다.

    uv run python -m pytest -q tests/test_timing.py
"""

import json

import httpx2
import openai

from negopilot_agent import timing
from negopilot_agent.decomposition.backends import OpenAIBackend

from tests.test_graph import make_app


async def test_graph_records_nodes_and_tool_calls():
    app, query, _, caller = make_app("03_rank_then_law")
    with timing.collect() as t:
        await app.ainvoke({"query": query})

    nodes = [s.name for s in t.spans if s.kind == "node"]
    assert nodes == ["decompose_query", "route", "generate"]
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
