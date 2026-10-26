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
    assert {"list_laws", "get_article", "search_regulation", "list_articles"} <= run(go())


def test_list_laws_picks_version_in_force():
    laws = run(call("list_laws", {"as_of": "2026-10-20"}))
    by_key = {l["law_key"]: l for l in laws}
    sub = by_key["subcontract_act"]
    assert sub["in_force_effective_date"] <= "2026-10-20"
    # 2027-09-09 시행 예정 개정이 시행 예정으로 잡혀야 한다
    assert any(u["effective_date"] == "2027-09-09" for u in sub["upcoming"])


def test_list_articles_is_toc_of_version_in_force():
    toc = run(call("list_articles", {"law": "하도급법", "as_of": "2026-10-20"}))
    assert toc["law_key"] == "subcontract_act" and toc["effective_date"] <= "2026-10-20"
    by_no = {a["article_no"]: a["title"] for a in toc["articles"]}
    assert "제16조의2" in by_no and by_no["제4조"]
    assert all(a["title"] for a in toc["articles"])


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
    assert any(h.get("article_no") == "제4조" for h in hits)
    assert any(h.get("document_id") == "price_reduction" for h in hits)


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


def test_search_attaches_definition_of_terms_in_hits(monkeypatch):
    """연동 의무 조문만 찾으면 10% 기준이 빠진다. 결과에 나온 '주요 원재료'의 정의 항을 뒤에 붙인다 (#167)."""
    monkeypatch.setattr(server, "SUPPORTING_SOURCES", {})
    server._search_cache.clear()
    hits = run(call("search_regulation", {"query": "원재료 비중이 높은 부품이면 납품대금 연동 약정을 서면에 꼭 적어야 하나요?"}))
    regular = [h for h in hits if h["type"] != "정의"]
    definitions = [h for h in hits if h["type"] == "정의"]
    assert len(regular) == 5 and hits[:5] == regular          # 정의는 top_k 뒤에만 붙는다
    assert 0 < len(definitions) <= server.MAX_DEFINITIONS
    major = next(h for h in definitions if h["law_key"] == "subcontract_act")
    assert (major["article_no"], major["paragraph"]) == ("제2조", "⑯") and "100분의 10" in major["text"]
    # 상생협력법 제2조는 한 항에 모든 용어를 정의해, 그 용어를 정의한 호만 남긴다
    winwin = next(h for h in definitions if h["law_key"] == "winwin_act")
    assert winwin["text"].startswith('12. "주요 원재료"란') and "중소기업" not in winwin["text"]


def test_definition_text_keeps_only_the_defining_item():
    text = '이 법에서 사용하는 용어의 뜻은 다음과 같다.\n\n  1. "가나"란 첫째를 말한다.\n  2. "다라"란 다음을 말한다.\n    가. 하나\n    나. 둘\n  3. "마바"란 셋째를 말한다.'
    assert server.definition_text(text, "다라") == '2. "다라"란 다음을 말한다.\n가. 하나\n나. 둘'
    assert server.definition_text('이 법에서 "가나"란 첫째를 말한다.', "가나") == '이 법에서 "가나"란 첫째를 말한다.'


def test_common_terms_get_no_definition():
    _, _, definitions = server.search_units(("subcontract_act",), server.parse_as_of(None), False)
    terms = {t for _, t in definitions}
    assert "주요 원재료" in terms and "원사업자" not in terms and "수급사업자" not in terms


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


def test_no_admin_or_refresh_surface():
    async def go():
        async with Client(mcp) as client:
            return [t.name for t in await client.list_tools()]
    assert not any(name.startswith("admin_") for name in run(go()))
    assert not hasattr(server, "activate_dataset")


def test_business_wording_is_mapped_to_legal_terms():
    hits = run(call("search_regulation", {
        "query": "1차 협력사가 2차 협력사에 전 품목 5% 단가 인하를 요구해도 되나",
        "laws": ["하도급법"], "top_k": 3}))
    assert any(h["article_no"] == "제4조" for h in hits)


def test_supporting_source_effective_date_changes_search_cache(monkeypatch):
    doc = {'document_id': 'synthetic', 'title': 'QA', 'parent_laws': ['subcontract_act'],
           'effective_date': '2026-10-21', 'text': '주요 원재료'}
    monkeypatch.setattr(server, 'SUPPORTING_SOURCES', {'synthetic': doc})
    server._search_cache.clear()
    before = server.search_units(('subcontract_act',), '2026-10-20', False)
    after = server.search_units(('subcontract_act',), '2026-10-21', False)
    assert before is not after
    assert not any('document' in u for u in before[0])
    assert any('document' in u for u in after[0])
    assert server.search_units(('subcontract_act',), '2026-10-22', False) is after


def test_supporting_sources_do_not_push_statutes_out_of_top_k():
    """보조 자료 청크는 길어서 한 색인에 섞으면 조문 top_k를 차지했다 (#235). 조문을 먼저 채우고 보조 자료는 뒤에 붙인다."""
    server._search_cache.clear()
    hits = run(call("search_regulation", {"query": "주요 원재료 비중이 큰 부품이면 납품대금 연동 내용을 하도급 계약서에 반드시 적어야 하나요?",
                                          "as_of": "2026-10-23"}))
    statutes = [h for h in hits if not h.get("document_id")]
    supporting = [h for h in hits if h.get("document_id")]
    assert len([h for h in statutes if h["type"] == "조문"]) == 5
    assert any((h["law_key"], h["article_no"]) == ("subcontract_act", "제3조") for h in statutes)
    assert 0 < len(supporting) <= server.MIN_SUPPORTING
    assert hits[-len(supporting):] == supporting          # 보조 자료는 조문·정의 뒤에만 온다


def test_supporting_count_grows_with_top_k():
    server._search_cache.clear()
    hits = run(call("search_regulation", {"query": "하도급대금 연동 표준 연동계약서", "as_of": "2026-10-23", "top_k": 10}))
    assert sum(1 for h in hits if h.get("document_id")) <= 5



def test_list_laws_reports_in_force_promulgation():
    """시행 중인 판의 공포번호·공포일. 거래 사실 검토의 판본 표기에 쓴다 (#261)."""
    laws = {x["law_key"]: x for x in run(call("list_laws", {"as_of": "2026-10-23"}))}
    act = laws["subcontract_act"]
    assert (act["in_force_effective_date"], act["in_force_promulgation_no"]) == ("2026-10-02", "21857")
    assert act["in_force_promulgation_date"]
