"""검색 SQL과 결과 변환을 DB 없이 검증한다. tests/conftest.py를 사용하지 않는다."""

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError


SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
SPEC = importlib.util.spec_from_file_location("business_mcp_offline_server", SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = server
SPEC.loader.exec_module(server)

CAPABILITIES = [
    {"process": "다이캐스팅", "material": "ADC12"},
    {"process": "프레스", "material": "SPCC"},
    {"process": "프레스", "material": "SPFH590"},
    {"process": "사출", "material": "PA66"},
    {"process": "CNC가공", "material": "AL6061"},
]


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Offline tests must mock query and never open a database connection")

    monkeypatch.setattr(server, "query", blocked)
    monkeypatch.setattr(server.psycopg, "connect", blocked)


def mock_query(monkeypatch, *responses):
    calls = []
    pending = iter(responses)

    def query(sql, params=()):
        calls.append((" ".join(sql.split()), params))
        result = next(pending)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(server, "query", query)
    return calls


def candidate(supplier_id="SUP-0001", name="한빛정밀", region="경남", total=1):
    return {"supplier_id": supplier_id, "name": name, "region": region, "total": total}


def test_tool_schema_exposes_optional_process_and_read_only_lookup():
    async def inspect_tools():
        async with Client(server.mcp) as client:
            return {tool.name: tool for tool in await client.list_tools()}

    tools = asyncio.run(inspect_tools())
    assert "process" not in tools["search_suppliers"].input_schema.get("required", [])
    search_schema = tools["search_suppliers"].input_schema
    assert "distinct_suppliers" not in search_schema.get("required", [])
    assert search_schema["properties"]["distinct_suppliers"]["type"] == "boolean"
    assert search_schema["properties"]["distinct_suppliers"]["default"] is False
    assert tools["find_suppliers"].input_schema["required"] == ["supplier"]
    assert tools["find_suppliers"].input_schema["properties"]["limit"]["default"] == 10
    assert tools["find_suppliers"].annotations.read_only_hint


@pytest.mark.parametrize("supplier", ["SUP-0001", "sup1", "0001", " 1 "])
def test_find_normalizes_loose_id_without_name_search(monkeypatch, supplier):
    calls = mock_query(monkeypatch, [candidate()])

    assert server.find_suppliers(supplier) == {
        "matches": [{"supplier_id": "SUP-0001", "name": "한빛정밀", "region": "경남"}],
        "total": 1,
        "match_type": "id",
    }
    assert len(calls) == 1
    assert calls[0][1] == ("SUP-0001", 10)
    assert "WHERE supplier_id = %s ORDER BY supplier_id LIMIT %s" in calls[0][0]
    assert "count(*) OVER () AS total" in calls[0][0]


def test_exact_name_takes_precedence_even_with_multiple_exact_candidates(monkeypatch):
    calls = mock_query(monkeypatch, [candidate(total=2)])

    result = server.find_suppliers(" (주) 한빛 정밀 ", limit=1)

    assert result["match_type"] == "exact"
    assert result["total"] == 2
    assert len(result["matches"]) == 1
    assert set(result["matches"][0]) == {"supplier_id", "name", "region"}
    assert len(calls) == 1  # 정확 일치가 있으면 부분 검색을 실행하지 않는다.
    assert calls[0][1] == (server.COMPANY_SUFFIX, "한빛정밀", 1)
    assert "ILIKE %s ESCAPE '!'" in calls[0][0]
    assert "ORDER BY supplier_id LIMIT %s" in calls[0][0]


def test_partial_name_returns_limited_candidates_and_full_count(monkeypatch):
    calls = mock_query(monkeypatch, [], [
        candidate("SUP-0002", "한빛금속", total=7),
        candidate("SUP-0003", "한빛정밀", total=7),
    ])

    result = server.find_suppliers("한빛", limit=2)

    assert result["match_type"] == "partial"
    assert result["total"] == 7
    assert [row["supplier_id"] for row in result["matches"]] == ["SUP-0002", "SUP-0003"]
    assert [params for _, params in calls] == [
        (server.COMPANY_SUFFIX, "한빛", 2),
        (server.COMPANY_SUFFIX, "%한빛%", 2),
    ]
    assert all("count(*) OVER () AS total" in sql for sql, _ in calls)


@pytest.mark.parametrize(("supplier", "responses", "match_type"), [
    ("SUP-9999", [[]], "id"),
    ("없는 기업", [[], []], "partial"),
])
def test_no_matches_are_a_normal_result(monkeypatch, supplier, responses, match_type):
    mock_query(monkeypatch, *responses)
    assert server.find_suppliers(supplier) == {"matches": [], "total": 0, "match_type": match_type}


@pytest.mark.parametrize("supplier", ["", " \t\n", "(주)", "주식회사", " ㈜ "])
def test_blank_or_only_company_notation_is_an_input_error(supplier):
    with pytest.raises(ToolError, match="supplier 가 비어 있다"):
        server.find_suppliers(supplier)


def test_name_wildcards_and_quotes_remain_literal_query_parameters(monkeypatch):
    calls = mock_query(monkeypatch, [], [])

    server.find_suppliers("A%_!\\' OR 1=1")

    literal = "A!%!_!!\\'OR1=1"
    assert calls[0][1] == (server.COMPANY_SUFFIX, literal, 10)
    assert calls[1][1] == (server.COMPANY_SUFFIX, f"%{literal}%", 10)
    assert all("ESCAPE '!'" in sql and "OR1=1" not in sql for sql, _ in calls)


@pytest.mark.parametrize(("supplier", "before"), [("1", []), ("한빛", []), ("한빛", [[]])])
def test_lookup_database_errors_are_not_replaced_with_empty_matches(monkeypatch, supplier, before):
    failure = ToolError("test database unavailable")
    calls = mock_query(monkeypatch, *before, failure)

    with pytest.raises(ToolError) as raised:
        server.find_suppliers(supplier)

    assert raised.value is failure
    assert len(calls) == len(before) + 1


def test_search_without_process_accepts_region_and_certification(monkeypatch):
    rows = [{"supplier_id": "SUP-0001", "process": "프레스", "material": "SPCC"}]
    calls = mock_query(monkeypatch, rows)

    assert server.search_suppliers(region="경상남도", iatf_required=True) == rows

    sql, params = calls[0]
    assert "c.process = %s" not in sql
    assert "WHERE o.region = %s AND o.iatf16949" in sql
    assert params == ["경남", 20]


def test_search_with_no_filters_is_still_bounded_and_deterministic(monkeypatch):
    calls = mock_query(monkeypatch, [])
    assert server.search_suppliers() == []
    sql, params = calls[0]
    assert "WHERE" not in sql
    assert sql.endswith("ORDER BY o.defect_ppm, o.supplier_id, c.process, c.material LIMIT %s")
    assert params == [20]


@pytest.mark.parametrize(("material", "expected"), [
    ("알루미늄", ["ADC12", "AL6061"]),
    (" pa 66 ", ["PA66"]),
])
def test_material_without_process_uses_actual_supported_code_union(monkeypatch, material, expected):
    calls = mock_query(monkeypatch, CAPABILITIES, [])

    assert server.search_suppliers(material=material) == []

    assert "SELECT DISTINCT process, material FROM capabilities" in calls[0][0]
    assert calls[1][1] == [expected, 20]
    assert "WHERE c.material = ANY(%s)" in calls[1][0]
    assert "c.process = %s" not in calls[1][0]


def test_unknown_material_without_process_is_rejected_before_search(monkeypatch):
    calls = mock_query(monkeypatch, CAPABILITIES)
    with pytest.raises(ToolError, match="전체 공정에 소재 .*없다"):
        server.search_suppliers(material="unavailable")
    assert len(calls) == 1


def test_existing_process_material_and_other_filters_keep_their_meaning(monkeypatch):
    rows = [{"supplier_id": "SUP-0001", "process": "프레스", "material": "SPCC", "defect_ppm": 20}]
    calls = mock_query(monkeypatch, CAPABILITIES, rows)

    result = server.search_suppliers("스탬핑", "강판", "경상남도", True, 1000, 4)

    assert result == rows
    sql, params = calls[-1]
    assert params == ["프레스", ["SPCC", "SPFH590"], "경남", 1000, 4]
    assert "WHERE c.process = %s AND c.material = ANY(%s) AND o.region = %s" in sql
    assert "AND o.iatf16949 AND c.monthly_capacity >= %s" in sql
    assert sql.endswith("ORDER BY o.defect_ppm, o.supplier_id, c.process, c.material LIMIT %s")


def test_explicit_default_mode_preserves_duplicate_capability_rows(monkeypatch):
    rows = [
        {"supplier_id": "SUP-0001", "process": "프레스", "material": "SPCC"},
        {"supplier_id": "SUP-0001", "process": "프레스", "material": "SPFH590"},
    ]
    calls = mock_query(monkeypatch, rows, rows)

    assert server.search_suppliers(region="부산", limit=2) == rows
    assert server.search_suppliers(region="부산", limit=2, distinct_suppliers=False) == rows

    assert calls[0] == calls[1]
    assert "DISTINCT ON" not in calls[0][0]
    assert calls[0][1] == ["부산", 2]


def test_distinct_mode_applies_every_filter_before_representative_and_company_limit(monkeypatch):
    rows = [{"supplier_id": "SUP-0001", "process": "프레스", "material": "SPCC",
             "monthly_capacity": 2000, "defect_ppm": 20}]
    calls = mock_query(monkeypatch, CAPABILITIES, rows)

    result = server.search_suppliers("스탬핑", "강판", "경상남도", True, 1000, 3,
                                     distinct_suppliers=True)

    assert result == rows
    sql, params = calls[-1]
    assert sql.startswith("SELECT * FROM ( SELECT DISTINCT ON (o.supplier_id) o.supplier_id")
    assert "WHERE c.process = %s AND c.material = ANY(%s) AND o.region = %s" in sql
    assert "AND o.iatf16949 AND c.monthly_capacity >= %s ORDER BY o.supplier_id, c.process, c.material)" in sql
    assert sql.endswith("matched_suppliers ORDER BY defect_ppm, supplier_id LIMIT %s")
    assert sql.count("LIMIT") == 1
    assert params == ["프레스", ["SPCC", "SPFH590"], "경남", 1000, 3]


@pytest.mark.parametrize(("limit", "bounded"), [(-1, 1), (3, 3), (101, 100)])
def test_distinct_region_search_has_no_process_requirement_or_inner_limit(monkeypatch, limit, bounded):
    calls = mock_query(monkeypatch, [])

    assert server.search_suppliers(region="부산광역시", limit=limit, distinct_suppliers=True) == []

    sql, params = calls[0]
    assert "c.process = %s" not in sql
    assert "WHERE o.region = %s ORDER BY o.supplier_id, c.process, c.material)" in sql
    assert sql.count("LIMIT") == 1
    assert params == ["부산", bounded]


def test_distinct_material_only_search_keeps_supported_code_union(monkeypatch):
    calls = mock_query(monkeypatch, CAPABILITIES, [])

    assert server.search_suppliers(material="알루미늄", distinct_suppliers=True) == []

    assert calls[1][1] == [["ADC12", "AL6061"], 20]
    assert "WHERE c.material = ANY(%s) ORDER BY o.supplier_id, c.process, c.material)" in calls[1][0]


def test_distinct_search_does_not_convert_database_error_to_empty_list(monkeypatch):
    failure = ToolError("test database unavailable")
    mock_query(monkeypatch, failure)

    with pytest.raises(ToolError) as raised:
        server.search_suppliers(region="부산", distinct_suppliers=True)

    assert raised.value is failure


def test_material_for_another_process_does_not_leak_into_process_search(monkeypatch):
    calls = mock_query(monkeypatch, CAPABILITIES)
    with pytest.raises(ToolError, match="다이캐스팅 공정에 소재 'PA66'가 없다"):
        server.search_suppliers(process="다이캐스팅", material="PA66")
    assert len(calls) == 1


def test_invalid_explicit_process_still_fails_before_query():
    with pytest.raises(ToolError, match="알 수 없는 공정"):
        server.search_suppliers(process="용접")


@pytest.mark.parametrize(("limit", "bounded"), [(-1, 1), (0, 1), (1, 1), (100, 100), (101, 100)])
def test_search_and_lookup_limits_are_bounded_in_sql(monkeypatch, limit, bounded):
    calls = mock_query(monkeypatch, [], [])
    server.search_suppliers(process="CNC", limit=limit)
    server.find_suppliers("1", limit=limit)
    assert calls[0][1] == ["CNC가공", bounded]
    assert calls[1][1] == ("SUP-0001", bounded)


def test_search_database_error_is_propagated(monkeypatch):
    failure = ToolError("test database unavailable")
    mock_query(monkeypatch, failure)
    with pytest.raises(ToolError) as raised:
        server.search_suppliers(region="경남")
    assert raised.value is failure


def test_get_supplier_retains_its_flat_overview_and_detail_shape(monkeypatch):
    overview = {"supplier_id": "SUP-0001", "name": "한빛정밀", "payment_days": 30, "source": "MOCK"}
    capabilities = [{"process": "프레스", "material": "SPCC", "monthly_capacity": 1000}]
    financials = [{"bsns_year": 2025, "revenue": 1000000, "source": "MOCK"}]
    quotes = [{"unit_price": 100, "quoted_at": "2026-01-01"}]
    calls = mock_query(monkeypatch, [{"exists": 1}], [overview], capabilities, financials, quotes)

    assert server.get_supplier("1") == {
        **overview, "capabilities": capabilities, "financials": financials, "recent_quotes": quotes,
    }
    assert len(calls) == 5
    assert all(params == ("SUP-0001",) for _, params in calls)


@pytest.mark.parametrize(("supplier", "rows", "error"), [
    ("한빛", [{"supplier_id": "SUP-0001", "name": "한빛정밀"},
              {"supplier_id": "SUP-0002", "name": "한빛금속"}], "2곳이다"),
    ("없는 기업", [], "해당하는 협력사가 없다"),
    ("SUP-9999", [], "SUP-9999 협력사가 없다"),
])
def test_get_supplier_preserves_ambiguous_and_missing_errors(monkeypatch, supplier, rows, error):
    calls = mock_query(monkeypatch, rows)
    with pytest.raises(ToolError, match=error):
        server.get_supplier(supplier)
    assert len(calls) == 1
