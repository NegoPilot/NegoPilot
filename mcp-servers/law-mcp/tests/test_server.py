"""MCP Client로 서버 도구를 실제로 호출해 보는 테스트.

    uv run python -m pytest -q
"""

import asyncio
import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

import server
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


def test_glossary_prefers_longer_wording():
    # '1차 협력사' 안의 '협력사'로 수급사업자가 함께 붙지 않는다
    assert server.expand_query("1차 협력사 의무") == "1차 협력사 의무 원사업자"
    assert server.expand_query("2차협력사") == "2차협력사 수급사업자"
    # '납품단가' 안의 '단가'는 다시 쓰지 않는다
    assert server.expand_query("납품단가") == "납품단가 하도급대금 납품대금"


def test_glossary_keeps_question_order_and_drops_duplicates():
    expanded = server.expand_query("1차 협력사가 2차 협력사에 단가 인하 요구")
    assert expanded.endswith(" 원사업자 수급사업자 하도급대금 단가 인하 감액 하도급대금의 결정")
    assert server.expand_query("하청 협력사") == "하청 협력사 수급사업자 하도급 수급사업자"
    assert server.expand_query("관련 없는 질문") == "관련 없는 질문"


def test_search_cache_is_keyed_by_version_in_force():
    server._search_cache.clear()
    keys = ("subcontract_act",)
    first = server.search_units(keys, "2026-10-20", False)
    # 같은 시행판이면 기준일이 달라도 색인을 다시 만들지 않는다
    assert server.search_units(keys, "2026-10-21", False) is first
    assert len(server._search_cache) == 1


def test_search_cache_evicts_least_recently_used(monkeypatch):
    monkeypatch.setattr(server, "SEARCH_CACHE_SIZE", 2)
    server._search_cache.clear()
    a = server.search_units(("subcontract_act",), "2026-10-20", False)
    server.search_units(("winwin_act",), "2026-10-20", False)
    assert server.search_units(("subcontract_act",), "2026-10-20", False) is a   # a를 최근 사용으로 올림
    server.search_units(("sme_act",), "2026-10-20", False)                       # winwin_act가 밀려남
    assert len(server._search_cache) == 2
    assert [k[0] for k in server._search_cache] == [("subcontract_act",), ("sme_act",)]
    server._search_cache.clear()


def test_admin_tool_descriptions_are_korean():
    async def go():
        async with Client(mcp) as client:
            return {t.name: t.description for t in await client.list_tools() if t.name.startswith("admin_law_")}
    descriptions = run(go())
    assert len(descriptions) == 4
    assert all(d.startswith("관리자 화면용") for d in descriptions.values())


def test_business_wording_is_mapped_to_legal_terms():
    hits = run(call("search_regulation", {
        "query": "1차 협력사가 2차 협력사에 전 품목 5% 단가 인하를 요구해도 되나",
        "laws": ["하도급법"], "top_k": 3}))
    assert any(h["article_no"] == "제4조" for h in hits)
