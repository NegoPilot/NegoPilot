"""McpToolCaller 연결 재사용 테스트. 메모리 안의 FastMCP 서버로 실제 MCP 프로토콜을 돈다 (네트워크 없음).

    uv run python -m pytest -q tests/test_mcp_session.py
"""

import asyncio

import fastmcp
import pytest
from fastmcp import FastMCP

from negopilot_shared import DecompositionResult

from negopilot_agent.api.execution import SafeToolCaller
from negopilot_agent.routing import McpToolCaller, Router


def make_servers() -> dict[str, FastMCP]:
    business, law = FastMCP("business"), FastMCP("law")

    @business.tool
    def rank_suppliers(rfq_id: str, top_n: int = 3) -> dict:
        return {"rfq_id": rfq_id, "ranked": [{"supplier_id": "SUP-0001", "name": "가",
                                               "flags": [{"law_refs": [{"law": "subcontract_act",
                                                                         "article_no": "제4조"}]}]}]}

    @law.tool
    def get_article(law: str, article_no: str) -> dict:
        return {"law": law, "article_no": article_no, "text": "본문"}

    @law.tool
    def boom() -> dict:
        raise ValueError("실패")

    return {"business-mcp": business, "law-mcp": law}


class Opened(list):
    """새로 만든 fastmcp Client(=연결)의 서버 이름 목록. servers는 McpToolCaller에 넘길 주소 대신 쓰는 서버."""

    servers: dict[str, FastMCP]


@pytest.fixture
def opened(monkeypatch) -> Opened:
    names = {}
    opened = Opened()
    real = fastmcp.Client

    class CountingClient(real):
        def __init__(self, transport, *a, **kw):
            opened.append(names.get(id(transport), "?"))
            super().__init__(transport, *a, **kw)

    monkeypatch.setattr(fastmcp, "Client", CountingClient)
    servers = make_servers()
    names.update({id(s): n for n, s in servers.items()})
    opened.servers = servers
    return opened


async def test_without_session_each_call_connects(opened):
    caller = McpToolCaller(urls=opened.servers)
    for _ in range(3):
        assert (await caller("law-mcp", "get_article", {"law": "subcontract_act", "article_no": "제4조"}))["text"]
    assert opened == ["law-mcp"] * 3


async def test_session_reuses_one_connection_per_server(opened):
    caller = McpToolCaller(urls=opened.servers)
    async with caller.session():
        results = await asyncio.gather(*(
            caller("law-mcp", "get_article", {"law": "subcontract_act", "article_no": f"제{n}조"}) for n in range(1, 6)))
        await caller("business-mcp", "rank_suppliers", {"rfq_id": "RFQ-2026-001"})
        await caller("law-mcp", "get_article", {"law": "subcontract_act", "article_no": "제9조"})
    assert [r["article_no"] for r in results] == [f"제{n}조" for n in range(1, 6)]
    assert sorted(opened) == ["business-mcp", "law-mcp"]
    # 블록이 끝나면 닫히고, 다음 블록은 새로 연결한다
    async with caller.session():
        await caller("law-mcp", "get_article", {"law": "subcontract_act", "article_no": "제4조"})
    assert len(opened) == 3


async def test_tool_error_does_not_break_shared_connection(opened):
    caller = McpToolCaller(urls=opened.servers)
    async with caller.session():
        with pytest.raises(Exception):
            await caller("law-mcp", "boom", {})
        assert (await caller("law-mcp", "get_article", {"law": "subcontract_act", "article_no": "제4조"}))["text"]
    assert opened == ["law-mcp"]


async def test_router_uses_session_through_safe_caller(opened):
    decomposition = DecompositionResult.model_validate({
        "schema_version": "0.1", "status": "ok", "original_query": "RFQ-2026-001 추천",
        "sub_queries": [{"id": "q1", "intent": "supplier_ranking", "question": "RFQ-2026-001 협력사 순위",
                         "original_text": "RFQ-2026-001 추천", "entities": {"rfq_id": "RFQ-2026-001"},
                         "grounding": "required", "confidence": 0.9}]})
    result = await Router(SafeToolCaller(McpToolCaller(urls=opened.servers))).route(decomposition)
    outcome = result.outcomes[0]
    assert outcome.status == "ok"
    # 순위 결과의 law_refs로 조문을 미리 조회해도 서버별 연결은 하나다
    assert [c.tool for c in outcome.referenced_articles] == ["get_article"]
    assert sorted(opened) == ["business-mcp", "law-mcp"]
