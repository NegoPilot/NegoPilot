"""v2 live supplier lookup through business-mcp. A fake MCP session replaces the server and DB."""

import socket
from contextlib import asynccontextmanager

import httpx2 as httpx
import pytest

from negopilot_agent.api.app import create_app
from negopilot_agent.api.business_supplier_service import MCP_ROW_LIMIT, BusinessSupplierService
from negopilot_agent.api.settings import ApiSettings

API_KEY = "test-only-live-lookup-key"
HEADERS = {"X-Internal-Api-Key": API_KEY}
CATALOG = "/internal/v2/business/catalog"
SEARCH = "/internal/v2/suppliers"

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
            "defect_ppm": 150, "on_time_rate": 0.95, "audit_grade": "B", "years_dealing": 0}


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

    def __init__(self, *, failure=None, search_rows=None):
        self.calls = []
        self.failure = failure
        self.search_rows = SEARCH_ROWS if search_rows is None else search_rows

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
            matches = [{"supplier_id": "SUP-0001", "name": "공시기업", "region": "경남"}]
            hit = args["supplier"] == "SUP-0001"
            return {"matches": matches if hit else [], "total": int(hit), "match_type": "id"}
        if tool == "get_supplier":
            return DETAIL
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


@pytest.mark.parametrize("supplier_id", ["SUP-0002", "1", "sup-0001", "SUP-00001", "SUP0001"])
async def test_detail_accepts_only_existing_exact_ids(supplier_id):
    business = FakeBusiness()
    response = await get(live_app(business), SEARCH + "/" + supplier_id)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SUPPLIER_NOT_FOUND"
    assert ("get_supplier", {"supplier": supplier_id}) not in business.calls


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


@pytest.mark.parametrize("path", ["/internal/v1/business/catalog", "/internal/v1/suppliers/SUP-0034"])
async def test_v1_lookups_stay_stub_only_without_calling_business_mcp(path):
    business = FakeBusiness()
    response = await get(live_app(business), path)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEMO_MODE_REQUIRED"
    assert business.calls == []


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
