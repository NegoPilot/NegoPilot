"""ASGI contract tests. PostgreSQL paging itself is tested in business-mcp/tests."""

import json
import socket
from copy import deepcopy
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2 as httpx
import pytest

from negopilot_agent.api.app import create_app
from negopilot_agent.api.business_supplier_service import BusinessSupplierService
from negopilot_agent.api.settings import ApiSettings

API_KEY = "test-only-live-lookup-key"
HEADERS = {"X-Internal-Api-Key": API_KEY}
CATALOG = "/internal/business/catalog"
SEARCH = "/internal/suppliers"

CATALOG_RESOURCE = {
    "processes": {"다이캐스팅": ["A380", "ADC12"], "프레스": ["SPCC"], "사출": []},
    "regions": [{"region": "울산", "suppliers": 1}, {"region": "경남", "suppliers": 2}],
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
    "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-02T00:00:00+00:00",
    "recent_quotes": [{"quote_id": 7, "process": "다이캐스팅", "material": "ADC12", "unit_price": 4200, "quoted_at": "2026-03-02"}],
}

def summary(detail=None):
    raw = deepcopy(DETAIL if detail is None else detail)
    return raw | {
        "matched_capabilities": raw["capabilities"],
        "max_monthly_capacity": max((r["monthly_capacity"] for r in raw["capabilities"]), default=None),
        "financial_sources": [r["source"] for r in raw["financials"]],
        "has_contract": raw["payment_days"] is not None, "has_quotes": bool(raw["recent_quotes"]),
        "latest_quote": raw["recent_quotes"][0] if raw["recent_quotes"] else None,
    }


def page(rows, args):
    total = len(rows)
    items = rows[args["page"] * args["size"]:(args["page"] + 1) * args["size"]]
    return {**{key: args[key] for key in ("page", "size", "sort", "direction")}, "items": items,
            "returned_count": len(items), "total": total, "total_pages": (total + args["size"] - 1) // args["size"]}


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Live lookup tests must not contact business-mcp")
    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket.socket, "connect_ex", fail)


class FakeBusiness:
    def __init__(self, *, failure=None, search_rows=None, detail=None, mutate=None):
        self.calls = []
        self.failure = failure
        self.detail = deepcopy(DETAIL if detail is None else detail)
        self.rows = [summary(self.detail)] if search_rows is None else search_rows
        self.mutate = mutate

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
        if tool == "list_suppliers_page":
            result = page(self.rows, args)
        elif tool == "list_supplier_quotes":
            result = {**page(self.detail["recent_quotes"], args), "supplier_id": args["supplier_id"],
                      "supplier_exists": args["supplier_id"] == self.detail["supplier_id"]}
        elif tool == "find_suppliers":
            matches = [{key: self.detail[key] for key in ("supplier_id", "name", "region")}]
            hit = args["supplier"] == self.detail["supplier_id"]
            return {"matches": matches if hit else [], "total": int(hit), "match_type": "id"}
        elif tool == "get_supplier":
            return self.detail
        else:
            raise AssertionError(f"unexpected tool {tool}")
        if self.mutate:
            self.mutate(result)
        return result


def live_app(business, mode="live"):
    return create_app(settings=ApiSettings(mode=mode, internal_api_key=API_KEY),
                      supplier_lookup_service=BusinessSupplierService(session=business.session))


async def get(app, path, params=None, headers=HEADERS):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path, params=params, headers=headers)


def live_data(response):
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "0.4"
    assert body["mode"] == "live"
    return body["data"]


async def test_catalog_uses_live_schema_and_current_resources():
    assert live_data(await get(live_app(FakeBusiness()), CATALOG)) == {
        "processes": [{"process": "다이캐스팅", "materials": ["A380", "ADC12"]},
                      {"process": "프레스", "materials": ["SPCC"]}], "regions": ["경남", "울산"],
    }


async def test_no_filter_lists_suppliers_and_reads_one_paginated_tool():
    business = FakeBusiness()
    data = live_data(await get(live_app(business), SEARCH))
    assert data["total"] == data["returned_count"] == data["total_pages"] == 1
    assert data["page"] == 0 and data["size"] == 20 and data["sort"] == "name"
    assert "limit" not in data
    assert business.calls == [("list_suppliers_page", {
        "iatf_required": False, "iso14001_required": False, "page": 0, "size": 20,
        "sort": "name", "direction": "asc",
    })]
    first = data["items"][0]
    assert first["founded_year"] == 1990 and first["employees"] == 800
    assert first["quality"] == {"defect_ppm": 120, "on_time_delivery_rate": 95.7, "audit_grade": "A", "years_dealing": 3}
    assert first["existing_contract"]["raw_material_ratio"] == 0.45
    assert first["max_monthly_capacity"] == 20000
    assert first["latest_quote"]["quote_id"] == 7


@pytest.mark.parametrize("params", [
    {"process": "프레스"}, {"material": "SPCC"}, {"q": "SUP-"}, {"q": " 공시 "},
    {"region": "울산", "iatf_required": "true", "iso14001_required": "true", "min_monthly_capacity": "1000"},
    {"page": "3", "size": "50", "sort": "monthly_capacity", "direction": "desc"},
    {"process": "프레스", "material": "SPCC", "sort": "latest_quote_price"},
])
async def test_filters_and_order_are_forwarded_to_db_without_top_n_fanout(params):
    business = FakeBusiness(search_rows=[])
    data = live_data(await get(live_app(business), SEARCH, params))
    assert data["items"] == [] and data["total"] == 0
    tool, args = business.calls[0]
    assert tool == "list_suppliers_page" and len(business.calls) == 1
    for key, value in params.items():
        expected = int(value) if key in {"page", "size", "min_monthly_capacity"} else (
            value == "true" if key in {"iatf_required", "iso14001_required"} else value.strip())
        assert args[key] == expected


@pytest.mark.parametrize("q", ["", "  \t"])
async def test_blank_query_means_all_suppliers(q):
    business = FakeBusiness()
    live_data(await get(live_app(business), SEARCH, {"q": q}))
    assert "q" not in business.calls[0][1]


async def test_legacy_limit_is_only_a_page_size_alias():
    data = live_data(await get(live_app(FakeBusiness()), SEARCH, {"limit": "1"}))
    assert data["size"] == 1 and "limit" not in data


async def test_more_than_100_suppliers_can_be_paged_and_out_of_range_has_full_count():
    rows = [summary(DETAIL | {"supplier_id": f"SUP-{i:04d}"}) for i in range(1, 152)]
    app = live_app(FakeBusiness(search_rows=rows))
    data = live_data(await get(app, SEARCH, {"page": "1", "size": "100"}))
    assert data["total"] == 151 and data["returned_count"] == 51 and data["total_pages"] == 2
    assert data["items"][0]["supplier_id"] == "SUP-0101"
    beyond = live_data(await get(app, SEARCH, {"page": "2", "size": "100"}))
    assert beyond["items"] == [] and beyond["total"] == 151


async def test_detail_exposes_database_fields_with_units_and_independent_sources():
    data = live_data(await get(live_app(FakeBusiness()), SEARCH + "/SUP-0001"))
    assert data["quality"]["on_time_delivery_rate"] == 95.7
    assert data["corp_code"] == "00123456" and data["created_at"].endswith("+00:00")
    assert data["existing_contract"] == {"payment_days": 60, "price_linkage": False,
                                         "raw_material_ratio": 0.45, "auto_renewal": True}
    assert data["recent_quotes"][0]["quote_id"] == 7
    assert data["financials"][0]["debt_ratio"] is None
    assert data["financials"][1]["debt_ratio"] == 150
    assert data["financials"][1]["total_liabilities_krw"] == 150_000_000_000
    assert data["financials"][1]["fs_div"] == "OFS"
    assert data["provenance"] == {"profile": "DART", "financials": "DART", "operations": "MOCK"}


@pytest.mark.parametrize("supplier_id", ["SUP-10000", "SUP-123456789012345678"])
async def test_supplier_without_capability_quality_or_contract_is_visible(supplier_id):
    detail = DETAIL | {"supplier_id": supplier_id, "source": "MANUAL", "financials": [], "recent_quotes": [],
                       "capabilities": [], "payment_days": None, "price_linkage": None,
                       "defect_ppm": None, "on_time_rate": None, "audit_grade": None, "years_dealing": None,
                       "certification_source": "MANUAL", "capability_source": "MANUAL", "quality_source": "MOCK"}
    app = live_app(FakeBusiness(detail=detail))
    data = live_data(await get(app, SEARCH + "/" + supplier_id))
    assert data["capabilities"] == [] and all(v is None for v in data["quality"].values())
    assert data["existing_contract"] is None
    assert data["provenance"] == {"profile": "MANUAL", "financials": "UNKNOWN", "operations": "MANUAL"}
    item = live_data(await get(app, SEARCH))["items"][0]
    assert item["matched_capabilities"] == [] and item["max_monthly_capacity"] is None
    assert item["latest_quote"] is None


async def test_mixed_operations_and_financial_sources_are_preserved():
    detail = deepcopy(DETAIL)
    detail.update(source="MANUAL", capability_source="MANUAL")
    detail["financials"][0]["source"] = "MOCK"
    data = live_data(await get(live_app(FakeBusiness(detail=detail)), SEARCH + "/SUP-0001"))
    assert data["provenance"] == {"profile": "MANUAL", "financials": "MIXED", "operations": "MIXED"}


async def test_quote_history_is_paginated_independently_of_recent_five():
    quotes = [DETAIL["recent_quotes"][0] | {"quote_id": i} for i in range(1, 126)]
    business = FakeBusiness(detail=DETAIL | {"recent_quotes": quotes})
    data = live_data(await get(live_app(business), SEARCH + "/SUP-0001/quotes", {"page": "2", "size": "50"}))
    assert data["supplier_id"] == "SUP-0001" and data["total"] == 125 and data["returned_count"] == 25
    assert data["sort"] == "quoted_at" and data["direction"] == "desc"


@pytest.mark.parametrize("suffix", ["/SUP-0002", "/SUP-0002/quotes"])
async def test_nonexistent_or_inactive_supplier_returns_404(suffix):
    response = await get(live_app(FakeBusiness()), SEARCH + suffix)
    assert response.status_code == 404 and response.json()["error"]["code"] == "SUPPLIER_NOT_FOUND"


@pytest.mark.parametrize("supplier_id", ["1", "sup-0001", "SUP0001", "SUP-1234567890123456789"])
@pytest.mark.parametrize("suffix", ["", "/quotes"])
async def test_invalid_id_is_rejected_before_mcp(supplier_id, suffix):
    business = FakeBusiness()
    assert (await get(live_app(business), SEARCH + "/" + supplier_id + suffix)).status_code == 400
    assert business.calls == []


@pytest.mark.parametrize("mutate", [
    lambda raw: raw.update(total=100), lambda raw: raw.update(returned_count=0),
    lambda raw: raw.update(total_pages=5), lambda raw: raw.update(page=1, items=[], returned_count=0),
    lambda raw: raw.update(direction="desc"), lambda raw: raw.update(sort="region"),
    lambda raw: raw["items"][0].update(supplier_id="SUP-1"),
])
async def test_malformed_page_metadata_and_ids_are_rejected(mutate):
    response = await get(live_app(FakeBusiness(mutate=mutate)), SEARCH)
    assert response.status_code == 500


@pytest.mark.parametrize(("path", "params"), [
    (CATALOG, None), (SEARCH, None), (SEARCH + "/SUP-0001", None), (SEARCH + "/SUP-0001/quotes", None),
])
async def test_business_failure_is_retryable_without_private_details(path, params):
    response = await get(live_app(FakeBusiness(failure=RuntimeError("private-db-detail"))), path, params)
    assert response.status_code == 503 and response.json()["error"]["retryable"] is True
    assert "private-db-detail" not in response.text


@pytest.mark.parametrize("mode", ["stub", "live"])
async def test_lookup_always_reads_business_even_when_chat_mode_is_stub(mode):
    business = FakeBusiness()
    live_data(await get(live_app(business, mode), SEARCH))
    assert business.calls[0][0] == "list_suppliers_page"


@pytest.mark.parametrize("params", [
    {"process": ""}, {"material": " \t"}, {"region": ""}, {"q": "가" * 101},
    {"process": "가" * 101}, {"iatf_required": "True"}, {"iso14001_required": "1"},
    {"size": "0"}, {"size": "101"}, {"size": "1.5"}, {"page": "-1"}, {"page": "1000001"},
    {"page": "1.0"}, {"min_monthly_capacity": "0"}, {"min_monthly_capacity": "10000001"},
    {"sort": "name;DROP TABLE suppliers"}, {"direction": "ASC"}, {"sort": "latest_quote_price"},
    {"sort": "latest_quote_price", "process": "프레스"}, {"unknown": "private-query-value"},
    {"limit": "0"}, {"limit": "101"}, {"limit": "20", "page": "0"}, {"limit": "20", "size": "20"},
    [("q", "a"), ("q", "b")], [("page", "0"), ("page", "0")], [("size", "20"), ("size", "20")],
    [("process", "프레스"), ("process", "프레스")], [("limit", "1"), ("limit", "2")],
])
async def test_invalid_or_duplicate_query_is_rejected_before_mcp(params, caplog):
    business = FakeBusiness()
    response = await get(live_app(business), SEARCH, params)
    assert response.status_code == 400 and business.calls == []
    assert "private-query-value" not in response.text + caplog.text


@pytest.mark.parametrize("params", [
    {"sort": "unit_price_krw"}, {"sort": "unit_price_krw", "material": "ADC12"},
    {"q": "a"}, {"limit": "20"}, {"sort": "name"},
])
async def test_invalid_quote_filters_reject_before_mcp(params):
    business = FakeBusiness()
    assert (await get(live_app(business), SEARCH + "/SUP-0001/quotes", params)).status_code == 400
    assert business.calls == []


async def test_quote_price_sort_requires_both_filters_and_preserves_them():
    business = FakeBusiness()
    data = live_data(await get(live_app(business), SEARCH + "/SUP-0001/quotes", {
        "sort": "unit_price_krw", "process": "다이캐스팅", "material": "ADC12"}))
    assert data["sort"] == "unit_price_krw"
    assert business.calls[0][1]["process"] == "다이캐스팅"


@pytest.mark.parametrize(("suffix", "headers", "status", "code"), [
    ("foo/bar", HEADERS, 400, "VALIDATION_ERROR"), ("foo%2Fbar", HEADERS, 400, "VALIDATION_ERROR"),
    ("foo/bar", {}, 401, "UNAUTHORIZED"), ("SUP-0001/quotes", {}, 401, "UNAUTHORIZED"),
])
async def test_paths_require_authentication_and_valid_shape(suffix, headers, status, code):
    business = FakeBusiness()
    response = await get(live_app(business), SEARCH + "/" + suffix, headers=headers)
    assert response.status_code == status and response.json()["error"]["code"] == code
    assert business.calls == []


def test_published_contract_matches_sources_and_ids():
    from negopilot_agent.api.supplier_lookup_models import LiveProvenance, LiveSupplierDetail, LiveSupplierSummary
    contract = json.loads((Path(__file__).resolve().parents[2] / "docs/contracts/supplier-lookup.openapi.json").read_text())
    schemas = contract["components"]["schemas"]
    for field in ("profile", "financials", "operations"):
        assert schemas["Provenance"]["properties"][field]["enum"] == LiveProvenance.model_json_schema()["properties"][field]["enum"]
    for name, model in (("SupplierDetail", LiveSupplierDetail), ("SupplierSummary", LiveSupplierSummary)):
        actual = model.model_json_schema()["properties"]["supplier_id"]
        assert {key: schemas[name]["properties"]["supplier_id"][key] for key in ("pattern", "minLength", "maxLength")} == {
            key: actual[key] for key in ("pattern", "minLength", "maxLength")}
