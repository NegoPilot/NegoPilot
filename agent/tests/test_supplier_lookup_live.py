"""v2 live supplier lookup through business-mcp. A fake MCP session replaces the server and DB."""

import socket
import json
from copy import deepcopy
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2 as httpx
import pytest

from negopilot_agent.api.app import create_app
from negopilot_agent.api.business_supplier_service import MCP_ROW_LIMIT, BusinessSupplierService
from negopilot_agent.api.settings import ApiSettings

API_KEY = "test-only-live-lookup-key"
HEADERS = {"X-Internal-Api-Key": API_KEY}
CATALOG = "/internal/business/catalog"
SEARCH = "/internal/suppliers"

CATALOG_RESOURCE = {
    "processes": {"다이캐스팅": ["A380", "ADC12"], "프레스": ["SPCC"], "사출": [], "단조": [], "CNC가공": []},
    "process_aliases": {}, "material_aliases": {},
    "regions": [{"region": "울산", "suppliers": 1}, {"region": "경남", "suppliers": 2}],
    "suppliers_by_source": [], "rfqs": [],
}


def row(supplier_id, material, capacity, *, region="경남", source="MOCK", process="다이캐스팅", iatf=True,
        iso=False):
    return {"supplier_id": supplier_id, "source": source, "name": f"기업 {supplier_id}", "region": region,
            "employees": 120, "revenue_eok": 300, "iatf16949": iatf, "iso14001": iso, "operating_margin": 0.05,
            "debt_ratio": 1.2, "process": process, "material": material, "monthly_capacity": capacity,
            "defect_ppm": 150, "on_time_rate": 0.95, "audit_grade": "B", "years_dealing": 0,
            "certification_source": "MOCK", "capability_source": "MOCK", "quality_source": "MOCK",
            "financial_sources": [source], "has_contract": True, "has_quotes": True}


SEARCH_ROWS = {
    "ADC12": [row("SUP-0003", "ADC12", 5000), row("SUP-0001", "ADC12", 20000, source="DART")],
    "A380": [row("SUP-0001", "A380", 8000, source="DART"), row("SUP-0002", "A380", 3000, region="울산")],
}

DETAIL = {
    "supplier_id": "SUP-0001", "source": "DART", "corp_code": "00123456", "stock_code": "012345",
    "induty_code": "30332", "name": "공시기업", "region": "경남", "employees": 800, "revenue_eok": 5000,
    "founded_year": 1990, "biz_no_masked": "123-45-*****", "iatf16949": True, "iso14001": False,
    "defect_ppm": 120, "on_time_rate": 0.957, "audit_grade": "A", "years_dealing": 3,
    "payment_days": 60, "price_linkage": False, "raw_material_ratio": 0.45, "auto_renewal": True,
    "fin_year": 2024, "operating_margin": 0.05, "debt_ratio": 1.5,
    "certification_source": "MOCK", "capability_source": "MOCK", "quality_source": "MOCK",
    "capabilities": [{"process": "다이캐스팅", "material": "ADC12", "monthly_capacity": 20000}],
    "financials": [
        {"bsns_year": 2023, "fs_div": "OFS", "source": "DART", "revenue": 400_000_000_000,
         "operating_income": -2_000_000_000, "net_income": 1, "total_assets": 1,
         "total_liabilities": 300, "total_equity": 0},
        {"bsns_year": 2024, "fs_div": "OFS", "source": "DART", "revenue": 500_000_000_000,
         "operating_income": 25_000_000_000, "net_income": 1, "total_assets": 1,
         "total_liabilities": 150_000_000_000, "total_equity": 100_000_000_000},
    ],
    "recent_quotes": [{"process": "다이캐스팅", "material": "ADC12", "unit_price": 4200, "quoted_at": "2026-03-02"}],
}


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Live lookup tests must not contact business-mcp")

    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket.socket, "connect_ex", fail)


class FakeBusiness:
    """Records MCP calls and answers like business-mcp's tools."""

    def __init__(self, *, failure=None, search_rows=None, detail=None):
        self.calls = []
        self.failure = failure
        self.search_rows = SEARCH_ROWS if search_rows is None else search_rows
        self.detail = DETAIL if detail is None else detail

    @asynccontextmanager
    async def session(self):
        if self.failure is not None:
            raise self.failure
        yield self

    async def read_catalog(self):
        self.calls.append(("catalog", None))
        return CATALOG_RESOURCE

    async def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool == "search_suppliers":
            rows = self.search_rows.get(args["material"], [])
            return [item for item in rows if "region" not in args or item["region"] == args["region"]]
        if tool == "find_suppliers":
            matches = [{key: self.detail[key] for key in ("supplier_id", "name", "region")}]
            hit = args["supplier"] == self.detail["supplier_id"]
            return {"matches": matches if hit else [], "total": int(hit), "match_type": "id"}
        if tool == "get_supplier":
            return self.detail
        raise AssertionError(f"unexpected tool {tool}")


def live_app(business, mode="live"):
    return create_app(settings=ApiSettings(mode=mode, internal_api_key=API_KEY),
                      supplier_lookup_service=BusinessSupplierService(session=business.session))


async def get(app, path, params=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path, params=params, headers=HEADERS)


def live_data(response):
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "0.3"
    assert body["mode"] == "live"
    return body["data"]


async def test_catalog_drops_processes_without_materials_and_sorts():
    data = live_data(await get(live_app(FakeBusiness()), CATALOG))
    assert data == {
        "processes": [{"process": "다이캐스팅", "materials": ["A380", "ADC12"]},
                      {"process": "프레스", "materials": ["SPCC"]}],
        "regions": ["경남", "울산"],
    }


async def test_search_groups_capabilities_by_supplier_in_id_order():
    business = FakeBusiness()
    data = live_data(await get(live_app(business), SEARCH, {"process": "다이캐스팅", "limit": "2"}))
    assert [item["supplier_id"] for item in data["items"]] == ["SUP-0001", "SUP-0002"]
    assert data["returned_count"] == 2 and data["limit"] == 2
    first = data["items"][0]
    assert first["matched_capabilities"] == [
        {"process": "다이캐스팅", "material": "A380", "monthly_capacity": 8000},
        {"process": "다이캐스팅", "material": "ADC12", "monthly_capacity": 20000},
    ]
    assert first["provenance"] == {"profile": "DART", "financials": "DART", "operations": "MOCK"}
    tools = [call for call in business.calls if call[0] == "search_suppliers"]
    assert [args["material"] for _, args in tools] == ["A380", "ADC12"]
    assert all(args["limit"] == MCP_ROW_LIMIT and args["iatf_required"] is False for _, args in tools)


async def test_search_passes_filters_and_keeps_exact_region():
    business = FakeBusiness()
    params = {"process": "다이캐스팅", "material": "A380", "region": "울산",
              "iatf_required": "true", "iso14001_required": "true", "min_monthly_capacity": "1000"}
    data = live_data(await get(live_app(business), SEARCH, params))
    assert [item["supplier_id"] for item in data["items"]] == ["SUP-0002"]
    assert business.calls[-1] == ("search_suppliers", {
        "process": "다이캐스팅", "material": "A380", "region": "울산", "iatf_required": True,
        "iso14001_required": True, "min_monthly_capacity": 1000, "limit": MCP_ROW_LIMIT,
    })


@pytest.mark.parametrize("params", [
    {"process": "용접"},
    {"process": "다이캐스팅", "material": "SPCC"},
    {"process": "다이캐스팅", "region": "경상남도"},
])
async def test_unknown_filter_values_return_empty_without_calling_search(params):
    business = FakeBusiness()
    data = live_data(await get(live_app(business), SEARCH, params))
    assert data == {"items": [], "returned_count": 0, "limit": 20}
    assert [call[0] for call in business.calls] == ["catalog"]


async def test_possibly_truncated_search_is_an_internal_error():
    rows = {"ADC12": [row(f"SUP-{index:04d}", "ADC12", 5000) for index in range(MCP_ROW_LIMIT)]}
    response = await get(live_app(FakeBusiness(search_rows=rows)), SEARCH, {"process": "다이캐스팅"})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"


async def test_detail_maps_business_fields_to_contract():
    data = live_data(await get(live_app(FakeBusiness()), SEARCH + "/SUP-0001"))
    assert data == {
        "supplier_id": "SUP-0001", "name": "공시기업", "region": "경남", "iatf16949": True, "iso14001": False,
        "capabilities": [{"process": "다이캐스팅", "material": "ADC12", "monthly_capacity": 20000}],
        "quality": {"defect_ppm": 120, "on_time_delivery_rate": 95.7},
        "financials": [
            {"year": 2023, "revenue_krw": 400_000_000_000, "operating_profit_krw": -2_000_000_000,
             "debt_ratio": None, "source": "DART"},
            {"year": 2024, "revenue_krw": 500_000_000_000, "operating_profit_krw": 25_000_000_000,
             "debt_ratio": 150.0, "source": "DART"},
        ],
        "recent_quotes": [{"process": "다이캐스팅", "material": "ADC12", "unit_price_krw": 4200,
                           "quoted_at": "2026-03-02"}],
        "existing_contract": {"payment_days": 60, "price_linkage": False},
        "provenance": {"profile": "DART", "financials": "DART", "operations": "MOCK"},
    }


async def test_detail_without_contract_returns_null_contract():
    business = FakeBusiness()
    detail = DETAIL | {"payment_days": None, "price_linkage": None}

    async def call_tool(tool, args):
        return detail if tool == "get_supplier" else await FakeBusiness.call_tool(business, tool, args)

    business.call_tool = call_tool
    data = live_data(await get(live_app(business), SEARCH + "/SUP-0001"))
    assert data["existing_contract"] is None


@pytest.mark.parametrize("supplier_id", ["SUP-0002", "SUP-00001"])
async def test_detail_accepts_only_existing_exact_ids(supplier_id):
    business = FakeBusiness()
    response = await get(live_app(business), SEARCH + "/" + supplier_id)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SUPPLIER_NOT_FOUND"
    assert ("get_supplier", {"supplier": supplier_id}) not in business.calls


@pytest.mark.parametrize("supplier_id", ["1", "sup-0001", "SUP0001", "SUP-1234567890123456789"])
async def test_invalid_live_id_is_rejected_before_mcp(supplier_id):
    business = FakeBusiness()
    response = await get(live_app(business), SEARCH + "/" + supplier_id)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert business.calls == []


@pytest.mark.parametrize("supplier_id", ["SUP-10000", "SUP-123456789012345678"])
async def test_manual_supplier_without_quality_financials_or_contract_uses_real_sources(supplier_id):
    detail = DETAIL | {
        "supplier_id": supplier_id, "source": "MANUAL", "financials": [], "recent_quotes": [],
        "payment_days": None, "price_linkage": None, "defect_ppm": None, "on_time_rate": None,
        "certification_source": "MANUAL", "capability_source": "MANUAL", "quality_source": "MOCK",
    }
    business = FakeBusiness(detail=detail)
    data = live_data(await get(live_app(business), SEARCH + "/" + supplier_id))
    assert data["supplier_id"] == supplier_id
    assert data["quality"] == {"defect_ppm": None, "on_time_delivery_rate": None}
    assert data["financials"] == [] and data["recent_quotes"] == []
    assert data["existing_contract"] is None
    assert data["provenance"] == {"profile": "MANUAL", "financials": "UNKNOWN", "operations": "MANUAL"}
    assert business.calls == [("find_suppliers", {"supplier": supplier_id, "limit": 1}),
                              ("get_supplier", {"supplier": supplier_id})]


async def test_financial_and_operations_provenance_do_not_follow_profile_source():
    detail = deepcopy(DETAIL)
    detail.update(source="MANUAL", capability_source="MANUAL")
    detail["financials"][0]["source"] = "MOCK"
    data = live_data(await get(live_app(FakeBusiness(detail=detail)), SEARCH + "/SUP-0001"))
    assert data["provenance"] == {"profile": "MANUAL", "financials": "MIXED", "operations": "MIXED"}
    assert [entry["source"] for entry in data["financials"]] == ["MOCK", "DART"]


@pytest.mark.parametrize(("has_contract", "has_quotes"), [(True, False), (False, True)])
async def test_retained_mock_contract_or_quotes_prevent_manual_only_operations(has_contract, has_quotes):
    detail = DETAIL | {"certification_source": "MANUAL", "capability_source": "MANUAL",
                       "quality_source": "MANUAL"}
    if not has_contract:
        detail["payment_days"] = None
    if not has_quotes:
        detail["recent_quotes"] = []
    data = live_data(await get(live_app(FakeBusiness(detail=detail)), SEARCH + "/SUP-0001"))
    assert data["provenance"]["operations"] == "MIXED"


async def test_search_aggregates_actual_sources_and_includes_manual_supplier_without_quality():
    manual = row("SUP-10000", "ADC12", 5000, source="MANUAL") | {
        "financial_sources": [], "has_contract": False, "has_quotes": False,
        "certification_source": "MANUAL", "capability_source": "MANUAL",
        "defect_ppm": None, "on_time_rate": None,
    }
    updated = row("SUP-0001", "ADC12", 5000, source="MANUAL") | {
        "financial_sources": ["DART", "MOCK"], "capability_source": "MANUAL",
    }
    business = FakeBusiness(search_rows={"ADC12": [manual, updated]})
    items = live_data(await get(live_app(business), SEARCH, {"process": "다이캐스팅"}))["items"]
    assert [item["supplier_id"] for item in items] == ["SUP-0001", "SUP-10000"]
    assert [item["provenance"] for item in items] == [
        {"profile": "MANUAL", "financials": "MIXED", "operations": "MIXED"},
        {"profile": "MANUAL", "financials": "UNKNOWN", "operations": "MANUAL"},
    ]


async def test_inactive_or_missing_supplier_is_not_read_after_active_lookup_misses():
    business = FakeBusiness()
    response = await get(live_app(business), SEARCH + "/SUP-10000")
    assert response.status_code == 404
    assert business.calls == [("find_suppliers", {"supplier": "SUP-10000", "limit": 1})]


@pytest.mark.parametrize("supplier_id", ["SUP-1", "A-1", "SUP-1234567890123456789"])
async def test_invalid_supplier_id_in_mcp_search_result_is_not_returned(supplier_id):
    business = FakeBusiness(search_rows={"ADC12": [row(supplier_id, "ADC12", 5000)]})
    response = await get(live_app(business), SEARCH, {"process": "다이캐스팅"})
    assert response.status_code == 500


def test_published_contract_matches_live_models_for_sources_and_ids():
    from negopilot_agent.api.supplier_lookup_models import LiveProvenance, LiveSupplierDetail, LiveSupplierSummary

    contract = json.loads((Path(__file__).resolve().parents[2] / "docs/contracts/supplier-lookup.openapi.json").read_text())
    schemas = contract["components"]["schemas"]
    for field in ("profile", "financials", "operations"):
        assert schemas["Provenance"]["properties"][field]["enum"] == LiveProvenance.model_json_schema()["properties"][field]["enum"]
    for name, model in (("SupplierDetail", LiveSupplierDetail), ("SupplierSummary", LiveSupplierSummary)):
        actual = model.model_json_schema()["properties"]["supplier_id"]
        assert {key: schemas[name]["properties"]["supplier_id"][key] for key in ("pattern", "minLength", "maxLength")} == {
            key: actual[key] for key in ("pattern", "minLength", "maxLength")
        }


@pytest.mark.parametrize(("path", "params"), [
    (CATALOG, None), (SEARCH, {"process": "다이캐스팅"}), (SEARCH + "/SUP-0001", None),
])
async def test_business_mcp_failure_is_retryable_unavailable(path, params):
    business = FakeBusiness(failure=RuntimeError("private-db-detail"))
    response = await get(live_app(business), path, params)
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "UPSTREAM_UNAVAILABLE" and error["retryable"] is True
    assert "private-db-detail" not in response.text


@pytest.mark.parametrize("mode", ["stub", "live"])
async def test_v2_reads_business_mcp_in_either_execution_mode(mode):
    business = FakeBusiness()
    data = live_data(await get(live_app(business, mode), SEARCH + "/SUP-0001"))
    assert data["supplier_id"] == "SUP-0001"
    assert [call[0] for call in business.calls] == ["find_suppliers", "get_supplier"]


@pytest.mark.parametrize("params", [
    {},
    {"process": ""},
    {"process": " \t"},
    {"process": "가" * 101},
    {"process": "다이캐스팅", "material": ""},
    {"process": "다이캐스팅", "material": " \t"},
    {"process": "다이캐스팅", "material": "가" * 101},
    {"process": "다이캐스팅", "region": ""},
    {"process": "다이캐스팅", "region": "가" * 101},
    {"process": "다이캐스팅", "iatf_required": "1"},
    {"process": "다이캐스팅", "iatf_required": "True"},
    {"process": "다이캐스팅", "iatf_required": "yes"},
    {"process": "다이캐스팅", "iso14001_required": "1"},
    {"process": "다이캐스팅", "iso14001_required": "True"},
    {"process": "다이캐스팅", "limit": "0"},
    {"process": "다이캐스팅", "limit": "101"},
    {"process": "다이캐스팅", "limit": "1.5"},
    {"process": "다이캐스팅", "limit": "true"},
    {"process": "다이캐스팅", "min_monthly_capacity": "0"},
    {"process": "다이캐스팅", "min_monthly_capacity": "10000001"},
    {"process": "다이캐스팅", "min_monthly_capacity": "-1"},
    {"process": "다이캐스팅", "min_monthly_capacity": "1.5"},
    {"process": "다이캐스팅", "min_monthly_capacity": ""},
    [("process", "다이캐스팅"), ("min_monthly_capacity", "1"), ("min_monthly_capacity", "2")],
    {"process": "다이캐스팅", "unknown": "private-query-value"},
    {"process": "다이캐스팅", "offset": "0"},
    [("process", "다이캐스팅"), ("process", "다이캐스팅")],
    [("process", "다이캐스팅"), ("limit", "1"), ("limit", "2")],
    [("process", "다이캐스팅"), ("material", "ADC12"), ("material", "ADC12")],
    [("process", "다이캐스팅"), ("region", "경남"), ("region", "경남")],
    [("process", "다이캐스팅"), ("iatf_required", "false"), ("iatf_required", "false")],
])
async def test_search_rejects_invalid_or_duplicate_parameters_before_mcp(params, caplog):
    business = FakeBusiness()
    response = await get(live_app(business), SEARCH, params=params)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "private-query-value" not in response.text + caplog.text
    assert business.calls == []


async def test_search_accepts_string_length_boundary_after_trimming():
    data = live_data(await get(live_app(FakeBusiness()), SEARCH, params={
        "process": " " + "가" * 100 + " ", "material": "나" * 100, "region": "다" * 100,
    }))
    assert data == {"items": [], "returned_count": 0, "limit": 20}


@pytest.mark.parametrize(("suffix", "headers", "status", "code"), [
    ("foo/bar", HEADERS, 400, "VALIDATION_ERROR"),
    ("foo%2Fbar", HEADERS, 400, "VALIDATION_ERROR"),
    ("foo/bar", {}, 401, "UNAUTHORIZED"),
])
async def test_v2_slash_supplier_path_is_rejected_after_authentication(suffix, headers, status, code):
    business = FakeBusiness()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=live_app(business)),
                                 base_url="http://test") as client:
        response = await client.get(SEARCH + "/" + suffix, headers=headers)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert business.calls == []
