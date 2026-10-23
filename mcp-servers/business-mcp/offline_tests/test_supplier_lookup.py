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
REAL_QUERY = server.query
SPEC_DB_POOL = server.db_pool

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
    monkeypatch.setattr(server, "db_pool", blocked)
    # 소재 목록 캐시가 앞 테스트의 가짜 응답을 다음 테스트로 넘기지 않게 한다
    monkeypatch.setattr(server, "_materials_cache", None)


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
    assert "WHERE supplier_id = %s AND status = 'ACTIVE' ORDER BY supplier_id LIMIT %s" in calls[0][0]
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
    assert "WHERE o.status = 'ACTIVE' AND o.region = %s AND o.iatf16949" in sql
    assert params == ["경남", 20]


def test_iso14001_filter_and_column_are_added_only_when_required(monkeypatch):
    rows = [{"supplier_id": "SUP-0001", "iso14001": True}]
    calls = mock_query(monkeypatch, rows, rows)

    assert server.search_suppliers(region="부산", iatf_required=True, iso14001_required=True) == rows
    assert server.search_suppliers(region="부산") == rows

    assert "o.iatf16949, o.iso14001," in calls[0][0]
    assert "WHERE o.status = 'ACTIVE' AND o.region = %s AND o.iatf16949 AND o.iso14001 ORDER BY" in calls[0][0]
    assert "AND o.iso14001" not in calls[1][0]
    assert calls[0][1] == calls[1][1] == ["부산", 20]


def test_search_with_no_filters_is_still_bounded_and_deterministic(monkeypatch):
    calls = mock_query(monkeypatch, [])
    assert server.search_suppliers() == []
    sql, params = calls[0]
    assert "WHERE o.status = 'ACTIVE'" in sql
    assert sql.endswith("ORDER BY o.defect_ppm, o.supplier_id, c.process, c.material LIMIT %s")
    assert params == [20]


def test_search_returns_independent_source_metadata_without_quality_join(monkeypatch):
    rows = [{"supplier_id": "SUP-10000", "source": "MANUAL", "defect_ppm": None,
             "financial_sources": [], "has_contract": False, "has_quotes": False, "quote_sources": []}]
    calls = mock_query(monkeypatch, rows)
    assert server.search_suppliers() == rows
    sql = calls[0][0]
    assert "ARRAY(SELECT DISTINCT f.source FROM financials f WHERE f.supplier_id = o.supplier_id ORDER BY f.source) AS financial_sources" in sql
    assert "EXISTS(SELECT 1 FROM contracts ct WHERE ct.supplier_id = o.supplier_id) AS has_contract" in sql
    assert "EXISTS(SELECT 1 FROM quotes qt WHERE qt.supplier_id = o.supplier_id) AS has_quotes" in sql
    assert "ARRAY(SELECT DISTINCT qt.source FROM quotes qt WHERE qt.supplier_id = o.supplier_id ORDER BY qt.source) AS quote_sources" in sql
    assert "JOIN quality" not in sql
    assert "WHERE o.status = 'ACTIVE'" in sql


@pytest.mark.parametrize("supplier_id", ["SUP-10000", "SUP-123456789012345678"])
def test_long_canonical_id_uses_active_exact_query(monkeypatch, supplier_id):
    calls = mock_query(monkeypatch, [candidate(supplier_id=supplier_id)])
    result = server.find_suppliers(supplier_id)
    assert result["matches"][0]["supplier_id"] == supplier_id
    assert result["match_type"] == "id"
    assert calls[0][1] == (supplier_id, 10)
    assert "WHERE supplier_id = %s AND status = 'ACTIVE'" in calls[0][0]


@pytest.mark.parametrize(("material", "expected"), [
    ("알루미늄", ["ADC12", "AL6061"]),
    (" pa 66 ", ["PA66"]),
])
def test_material_without_process_uses_actual_supported_code_union(monkeypatch, material, expected):
    calls = mock_query(monkeypatch, CAPABILITIES, [])

    assert server.search_suppliers(material=material) == []

    assert "SELECT DISTINCT c.process, c.material FROM capabilities c JOIN suppliers s" in calls[0][0]
    assert calls[1][1] == [expected, 20]
    assert "WHERE o.status = 'ACTIVE' AND c.material = ANY(%s)" in calls[1][0]
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
    assert "WHERE o.status = 'ACTIVE' AND c.process = %s AND c.material = ANY(%s) AND o.region = %s" in sql
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
    assert "WHERE o.status = 'ACTIVE' AND c.process = %s AND c.material = ANY(%s) AND o.region = %s" in sql
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
    assert "WHERE o.status = 'ACTIVE' AND o.region = %s ORDER BY o.supplier_id, c.process, c.material)" in sql
    assert sql.count("LIMIT") == 1
    assert params == ["부산", bounded]


def test_distinct_material_only_search_keeps_supported_code_union(monkeypatch):
    calls = mock_query(monkeypatch, CAPABILITIES, [])

    assert server.search_suppliers(material="알루미늄", distinct_suppliers=True) == []

    assert calls[1][1] == [["ADC12", "AL6061"], 20]
    assert "WHERE o.status = 'ACTIVE' AND c.material = ANY(%s) ORDER BY o.supplier_id, c.process, c.material)" in calls[1][0]


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
    calls = mock_query(monkeypatch, [candidate()], [overview], capabilities, financials, quotes,
                       [{"source": "MANUAL"}, {"source": "MOCK"}])

    assert server.get_supplier("1") == {
        **overview, "flags": [], "law_refs": [],
        "capabilities": capabilities, "financials": financials, "recent_quotes": quotes,
        "quote_sources": ["MANUAL", "MOCK"],
    }
    assert len(calls) == 6
    assert "ORDER BY quoted_at DESC, quote_id DESC LIMIT 5" in calls[4][0]
    assert calls[5][0] == "SELECT DISTINCT source FROM quotes WHERE supplier_id = %s ORDER BY source"
    assert calls[0][1] == ("SUP-0001", server.SUPPLIER_CANDIDATES_SHOWN)   # find_suppliers와 같은 ID 조회
    assert all(params == ("SUP-0001",) for _, params in calls[1:])


def test_get_supplier_flags_existing_contract_terms(monkeypatch):
    """기존 계약 조건을 rank_suppliers와 같은 규칙으로 점검하고 조문을 law_refs로 모은다 (#180)."""
    overview = {"supplier_id": "SUP-0001", "name": "한빛정밀", "payment_days": 90, "years_dealing": 3,
                "price_linkage": False, "raw_material_ratio": 0.4, "source": "MOCK"}
    mock_query(monkeypatch, [candidate()], [overview], [], [], [], [])

    r = server.get_supplier("1")
    assert [f["code"] for f in r["flags"]] == ["PAYMENT_DAYS_OVER_60", "LINKAGE_TERMS_MISSING"]
    refs = {(ref["law"], ref["article_no"]) for f in r["flags"] for ref in f["law_refs"]}
    assert {(ref["law"], ref["article_no"]) for ref in r["law_refs"]} == refs
    assert len(r["law_refs"]) == len(refs)


@pytest.mark.parametrize(("supplier", "responses", "error"), [
    # 정확 일치가 여러 곳
    ("한빛", [[candidate("SUP-0001", "한빛", total=2), candidate("SUP-0002", "한빛", total=2)]],
     r"\[SUPPLIER_AMBIGUOUS\] '한빛'에 해당하는 협력사가 2곳이다.*SUP-0001 한빛, SUP-0002 한빛"),
    # 정확 일치는 없고 부분 일치가 여러 곳
    ("한빛", [[], [candidate("SUP-0001", "한빛정밀", total=12), candidate("SUP-0002", "한빛금속", total=12)]],
     r"\[SUPPLIER_AMBIGUOUS\] .*12곳이다"),
    ("없는 기업", [[], []], r"\[SUPPLIER_NOT_FOUND\] '없는 기업'에 해당하는 협력사가 없다"),
    ("SUP-9999", [[]], r"\[SUPPLIER_NOT_FOUND\] SUP-9999 협력사가 없다"),
    ("sup 1", [[], []], r"\[SUPPLIER_NOT_FOUND\]"),   # 공백이 있으면 ID가 아니라 이름으로 찾는다
])
def test_get_supplier_preserves_ambiguous_and_missing_errors(monkeypatch, supplier, responses, error):
    calls = mock_query(monkeypatch, *responses)
    with pytest.raises(ToolError, match=error):
        server.get_supplier(supplier)
    assert len(calls) == len(responses)


@pytest.mark.parametrize(("supplier", "responses"), [
    ("SUP-0001", [[candidate()]]),
    (" (주) 한빛 정밀 ", [[candidate()]]),
    ("한빛", [[], [candidate("SUP-0003", "한빛정밀")]]),
])
def test_find_and_resolve_use_the_same_rule(monkeypatch, supplier, responses):
    find_calls = mock_query(monkeypatch, *responses)
    found = server.find_suppliers(supplier)
    resolve_calls = mock_query(monkeypatch, *responses)
    resolved = server.resolve_supplier_id(supplier)

    assert found["total"] == 1
    assert resolved == found["matches"][0]["supplier_id"]
    # 같은 SQL을 같은 순서로 부른다 (보여 줄 후보 수만 다를 수 있다)
    assert [sql for sql, _ in find_calls] == [sql for sql, _ in resolve_calls]
    assert [params[:-1] for _, params in find_calls] == [params[:-1] for _, params in resolve_calls]


def test_exact_name_wins_over_partial_matches_for_resolve(monkeypatch):
    # 예전 resolve는 부분 일치 목록에서 정확 일치를 골랐다. 이제 find_suppliers처럼 정확 일치를 먼저 찾는다
    calls = mock_query(monkeypatch, [candidate("SUP-0007", "한빛")])
    assert server.resolve_supplier_id("(주)한빛") == "SUP-0007"
    assert calls[0][1] == (server.COMPANY_SUFFIX, "한빛", server.SUPPLIER_CANDIDATES_SHOWN)


def test_material_list_is_cached_within_ttl(monkeypatch):
    calls = mock_query(monkeypatch, CAPABILITIES, [], [])
    server.search_suppliers(material="ADC12")
    server.search_suppliers(material="PA66")
    assert sum("SELECT DISTINCT c.process, c.material" in sql for sql, _ in calls) == 1


def test_material_list_is_reloaded_after_ttl(monkeypatch):
    monkeypatch.setattr(server, "MATERIALS_TTL_SECONDS", 0)
    calls = mock_query(monkeypatch, CAPABILITIES, [], CAPABILITIES, [])
    server.search_suppliers(material="ADC12")
    server.search_suppliers(material="ADC12")
    assert sum("SELECT DISTINCT c.process, c.material" in sql for sql, _ in calls) == 2


def test_material_added_after_caching_is_found_without_waiting_for_ttl(monkeypatch):
    added = CAPABILITIES + [{"process": "단조", "material": "SCM440"}]
    calls = mock_query(monkeypatch, CAPABILITIES, [], added, [])
    server.search_suppliers(material="ADC12")              # 캐시에 SCM440 없음
    assert server.search_suppliers(material="SCM440") == []   # 캐시에 없으니 DB를 한 번 더 읽는다
    assert calls[-1][1] == [["SCM440"], 20]


def test_unknown_material_with_warm_cache_reloads_once_then_fails(monkeypatch):
    calls = mock_query(monkeypatch, CAPABILITIES, [], CAPABILITIES)
    server.search_suppliers(material="ADC12")
    with pytest.raises(ToolError, match="전체 공정에 소재 'unavailable'가 없다"):
        server.search_suppliers(material="unavailable")
    assert len(calls) == 3


def test_catalog_always_reads_current_materials(monkeypatch):
    calls = mock_query(monkeypatch, CAPABILITIES, [], [], [], [], CAPABILITIES)
    server.search_suppliers(material="ADC12")     # 캐시를 채운다
    server.catalog()                               # regions, counts, rfqs, 소재 목록
    assert sum("SELECT DISTINCT c.process, c.material" in sql for sql, _ in calls) == 2


def test_database_down_is_reported_as_db_unavailable(monkeypatch):
    monkeypatch.setattr(server, "query", REAL_QUERY)
    monkeypatch.setattr(server, "db_pool", SPEC_DB_POOL)
    monkeypatch.setattr(server, "DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none?connect_timeout=1")
    monkeypatch.setattr(server, "DB_WAIT_SECONDS", 0.5)
    monkeypatch.setattr(server, "_pool", None)
    try:
        with pytest.raises(ToolError, match=r"^\[DB_UNAVAILABLE\]"):
            server.query("SELECT 1")
    finally:
        if server._pool is not None:
            server._pool.close()


def test_pool_is_not_opened_on_import():
    assert server._pool is None
