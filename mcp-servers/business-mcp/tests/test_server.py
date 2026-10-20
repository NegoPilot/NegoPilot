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


def test_tools_are_listed_and_read_only():
    async def go():
        async with Client(mcp) as client:
            return await client.list_tools()
    tools = {t.name: t for t in run(go())}
    assert {"list_rfqs", "search_suppliers", "get_supplier", "rank_suppliers"} <= set(tools)
    assert all(t.annotations and t.annotations.read_only_hint for t in tools.values())


def test_list_rfqs():
    rfqs = run(call("list_rfqs", {}))
    assert len(rfqs) == 6
    assert [r["due_date"] for r in rfqs] == sorted(r["due_date"] for r in rfqs)


def test_search_with_process_and_material_aliases():
    rows = run(call("search_suppliers", {"process": "스탬핑", "material": "강판", "limit": 100}))
    assert rows
    assert {r["process"] for r in rows} == {"프레스"}
    assert {r["material"] for r in rows} <= {"SPCC", "SPFH590"}


def test_search_with_region_full_name():
    rows = run(call("search_suppliers", {"process": "사출", "region": "경상남도", "limit": 100}))
    assert rows
    assert {r["region"] for r in rows} == {"경남"}


def test_unknown_process_is_a_clear_error():
    with pytest.raises(ToolError, match="알 수 없는 공정"):
        run(call("search_suppliers", {"process": "용접"}))


def test_unknown_material_lists_available_ones():
    with pytest.raises(ToolError, match="사용 가능: .*ADC12"):
        run(call("search_suppliers", {"process": "다이캐스팅", "material": "PA66"}))


def test_get_supplier_by_loose_id_and_by_name():
    by_id = run(call("get_supplier", {"supplier": "1"}))
    assert by_id["supplier_id"] == "SUP-0001"
    assert by_id["capabilities"] and by_id["financials"]
    name = by_id["name"].replace("(주)", "").replace("주식회사", "").strip()
    by_name = run(call("get_supplier", {"supplier": name}))
    assert by_name["supplier_id"] == "SUP-0001"


def test_ambiguous_name_lists_candidates():
    with pytest.raises(ToolError, match="곳이다"):
        run(call("get_supplier", {"supplier": "다이캐스팅"}))


def test_missing_supplier_is_a_clear_error():
    with pytest.raises(ToolError, match="없다"):
        run(call("get_supplier", {"supplier": "SUP-9999"}))


def test_rank_suppliers_applies_cert_and_capacity_rules():
    out = run(call("rank_suppliers", {"rfq_id": "rfq-2026-001"}))
    assert out["rfq"]["rfq_id"] == "RFQ-2026-001"
    need = out["needed_monthly_capacity"]
    assert need == 10000
    for c in out["ranked"]:
        assert c["iatf16949"] and c["monthly_capacity"] >= need
    scores = [c["total_score"] for c in out["ranked"]]
    assert scores == sorted(scores, reverse=True)
    assert all(e["reason"] for e in out["excluded"])
    assert out["candidate_count"] + len(out["excluded"]) > 0


def test_unknown_rfq_lists_available_ids():
    with pytest.raises(ToolError, match="RFQ-2026-001"):
        run(call("rank_suppliers", {"rfq_id": "RFQ-1999-999"}))


def test_catalog_resource():
    async def go():
        async with Client(mcp) as client:
            return await client.read_resource("business://catalog")
    data = json.loads(run(go())[0].text)
    assert set(data["processes"]) == {"다이캐스팅", "프레스", "사출", "단조", "CNC가공"}
    assert len(data["rfqs"]) == 6
