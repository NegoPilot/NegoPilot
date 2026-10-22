"""MCP Client로 서버 도구를 실제로 호출해 보는 테스트.

    uv run python -m pytest -q
"""

import asyncio
import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from server import mcp


def run(coro):
    return asyncio.run(coro)


async def call(tool: str, args: dict):
    async with Client(mcp) as client:
        result = await client.call_tool(tool, args)
        return result.data if result.data is not None else result.structured_content


def test_tools_are_listed():
    async def go():
        async with Client(mcp) as client:
            return {t.name for t in await client.list_tools()}
    assert {"list_laws", "get_article", "search_regulation"} <= run(go())


def test_list_laws_picks_version_in_force():
    laws = run(call("list_laws", {"as_of": "2026-10-20"}))
    by_key = {l["law_key"]: l for l in laws}
    sub = by_key["subcontract_act"]
    assert sub["in_force_effective_date"] <= "2026-10-20"
    # 2027-09-09 시행 예정 개정이 시행 예정으로 잡혀야 한다
    assert any(u["effective_date"] == "2027-09-09" for u in sub["upcoming"])


def test_get_article_with_alias_and_loose_number():
    art = run(call("get_article", {"law": "하도급법", "article_no": "4",
                                   "as_of": "2026-10-20"}))
    assert art["article_no"] == "제4조"
    assert "일률적인 비율로 단가를 인하" in json.dumps(art, ensure_ascii=False)
    assert art["citation"].startswith("「하도급거래 공정화에 관한 법률」 제4조")


def test_paragraphs_from_16_use_circled_numbers_in_every_version():
    # 2026-10-02 판 원문은 16항 이상을 "<16>"으로 적는다. 색인은 원문자로 맞춰 항을 나눠야 한다
    for as_of in ("2026-09-01", "2026-10-20"):
        art = run(call("get_article", {"law": "subcontract_act", "article_no": "제2조", "as_of": as_of}))
        paragraphs = {p["no"]: p["text"] for p in art["paragraphs"]}
        assert "주요 원재료" in paragraphs["⑯"]
        assert "주요 에너지" in paragraphs["⑰"]
        assert "<16>" not in json.dumps(art, ensure_ascii=False)


def test_get_article_branch_number():
    art = run(call("get_article", {"law": "subcontract_act", "article_no": "16조의2"}))
    assert art["article_no"] == "제16조의2"
    assert "공급원가" in json.dumps(art, ensure_ascii=False)


def test_search_finds_uniform_price_cut_rule():
    hits = run(call("search_regulation", {"query": "일률적인 비율로 단가 인하", "top_k": 3}))
    assert hits[0]["law_key"] == "subcontract_act"
    assert hits[0]["article_no"] == "제4조"


def test_search_finds_indexation_exceptions():
    hits = run(call("search_regulation", {"query": "하도급대금 연동 약정 적지 아니할 수 있는 경우 소기업 90일 1억",
                                          "laws": ["하도급법"], "top_k": 5}))
    assert any(h["article_no"] == "제3조" for h in hits)


def test_unknown_law_is_a_clear_error():
    with pytest.raises(ToolError, match="알 수 없는 법령"):
        run(call("get_article", {"law": "민법", "article_no": "1"}))


def test_missing_article_is_a_clear_error():
    with pytest.raises(ToolError, match="없다"):
        run(call("get_article", {"law": "하도급법", "article_no": "999"}))


def test_article_resource():
    async def go():
        async with Client(mcp) as client:
            return await client.read_resource("law://subcontract_act/제4조")
    contents = run(go())
    assert "부당한 하도급대금의 결정 금지" in contents[0].text


def test_business_wording_is_mapped_to_legal_terms():
    hits = run(call("search_regulation", {
        "query": "1차 협력사가 2차 협력사에 전 품목 5% 단가 인하를 요구해도 되나",
        "laws": ["하도급법"], "top_k": 3}))
    assert any(h["article_no"] == "제4조" for h in hits)
