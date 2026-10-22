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
    assert {"list_rfqs", "search_suppliers", "get_supplier", "rank_suppliers", "check_offer"} <= set(tools)
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


# ---------- check_offer ----------

def top_candidate(rfq_id="RFQ-2026-001"):
    return run(call("rank_suppliers", {"rfq_id": rfq_id, "top_n": 1}))["ranked"][0]["supplier_id"]


def offer(**kw):
    args = {"supplier": top_candidate(), "rfq_id": "RFQ-2026-001", "unit_price": 3800,
            "payment_days": 60, "price_linkage": True, "raw_material_ratio": 0.4}
    args.update(kw)
    return run(call("check_offer", args))


def codes(result, key):
    return [f["code"] for f in result[key]]


def test_clean_offer_has_no_violation():
    out = offer()
    assert out["verdict"] == "문제 없음"
    assert out["verdict_code"] == "OK"
    assert out["violations"] == [] and out["warnings"] == []
    assert out["offer"]["contract_amount"] == 120000 * 3800


def test_payment_over_60_days_is_a_violation_with_law_ref():
    out = offer(payment_days=75)
    assert "PAYMENT_DAYS_OVER_60" in codes(out, "violations")
    assert {"law": "subcontract_act", "article_no": "제13조", "paragraph": "①"} in out["law_refs"]


def test_missing_linkage_for_major_material_is_a_violation():
    out = offer(price_linkage=False)
    assert "LINKAGE_TERMS_MISSING" in codes(out, "violations")
    assert {"law": "subcontract_act", "article_no": "제3조", "paragraph": "②"} in out["law_refs"]
    assert out["verdict_code"] == "VIOLATION"


def test_linkage_waived_by_agreement_needs_written_reason():
    out = offer(price_linkage=False, linkage_waived_by_agreement=True)
    assert out["violations"] == []
    assert "LINKAGE_WAIVED_NEEDS_WRITTEN_REASON" in codes(out, "warnings")


def test_minor_material_does_not_need_linkage():
    out = offer(price_linkage=False, raw_material_ratio=0.05)
    assert out["violations"] == []


def test_short_contract_is_linkage_exempt():
    out = offer(price_linkage=False, contract_days=60)
    assert out["violations"] == []
    assert "LINKAGE_EXEMPT" in codes(out, "info")


def test_price_far_below_market_is_a_warning():
    out = offer(unit_price=2500)
    assert "PRICE_BELOW_MARKET" in codes(out, "warnings")
    assert {"law": "subcontract_act", "article_no": "제4조", "paragraph": "①"} in out["law_refs"]
    assert out["verdict_code"] == "CHECK_NEEDED"


def test_invalid_ratio_is_a_clear_error():
    with pytest.raises(ToolError, match="0~1"):
        offer(raw_material_ratio=40)


def test_v2_fields_and_basis():
    out = offer(as_of="2026-10-22")
    assert out["schema_version"] == "2"
    assert out["basis"] == {"as_of": "2026-10-22",
                            "law_versions": {"subcontract_act": "2026-10-02", "subcontract_decree": "2026-08-11",
                                             "sme_decree": "2026-01-02"}}
    assert out["offer"]["energy_ratio"] is None


def test_missing_energy_linkage_is_a_violation():
    out = offer(price_linkage=False, raw_material_ratio=0.05, energy_ratio=0.12, as_of="2026-10-22")
    assert codes(out, "violations") == ["ENERGY_LINKAGE_TERMS_MISSING"]
    assert {"law": "subcontract_act", "article_no": "제2조", "paragraph": "⑰"} in out["law_refs"]
    assert out["offer"]["energy_ratio"] == 0.12


def test_energy_linkage_exempt_and_waived():
    out = offer(price_linkage=False, raw_material_ratio=0.05, energy_ratio=0.12, contract_days=60, as_of="2026-10-22")
    assert "ENERGY_LINKAGE_EXEMPT" in codes(out, "info") and out["violations"] == []
    out = offer(price_linkage=False, raw_material_ratio=0.05, energy_ratio=0.12, linkage_waived_by_agreement=True,
                as_of="2026-10-22")
    assert out["violations"] == []
    assert "LINKAGE_WAIVED_NEEDS_WRITTEN_REASON" in codes(out, "warnings")


def test_unknown_energy_ratio_is_info_only():
    out = offer(price_linkage=False, raw_material_ratio=0.05, as_of="2026-10-22")
    assert "ENERGY_RATIO_UNKNOWN" in codes(out, "info")
    assert out["verdict_code"] == "OK"


def test_energy_rule_does_not_apply_before_it_took_effect():
    out = offer(price_linkage=False, raw_material_ratio=0.05, energy_ratio=0.12, as_of="2026-08-10")
    assert out["violations"] == []
    assert "ENERGY_RATIO_UNKNOWN" not in codes(out, "info")
    assert "ENERGY_RULE_NOT_IN_FORCE" in codes(out, "info")
    assert out["basis"]["law_versions"]["subcontract_act"] == "2025-12-17"


@pytest.mark.parametrize("kw, code", [
    ({"rfq_id": "RFQ-2026-999"}, "RFQ_NOT_FOUND"),
    ({"supplier": "SUP-9999"}, "SUPPLIER_NOT_FOUND"),
    ({"supplier": "없는회사이름"}, "SUPPLIER_NOT_FOUND"),
    ({"unit_price": 0}, "VALUE_OUT_OF_RANGE"),
    ({"energy_ratio": 1.5}, "VALUE_OUT_OF_RANGE"),
    ({"as_of": "2026/10/22"}, "VALUE_OUT_OF_RANGE"),
])
def test_input_errors_start_with_code(kw, code):
    with pytest.raises(ToolError, match=rf"^\[{code}\] "):
        offer(**kw)


def test_ambiguous_supplier_error_code():
    with pytest.raises(ToolError, match=r"^\[SUPPLIER_AMBIGUOUS\] "):
        run(call("check_offer", {"supplier": "다이캐스팅", "rfq_id": "RFQ-2026-001", "unit_price": 3800,
                                 "payment_days": 60, "price_linkage": True}))


def test_rank_suppliers_flags_have_law_refs():
    out = run(call("rank_suppliers", {"rfq_id": "RFQ-2026-004", "top_n": 50}))
    flags = [f for c in out["ranked"] for f in c["flags"]]
    assert all({"code", "severity", "message", "law_refs"} <= set(f) for f in flags)
    for c in out["ranked"]:
        if c["payment_days"] and c["payment_days"] > 60:
            assert "PAYMENT_DAYS_OVER_60" in [f["code"] for f in c["flags"]]
