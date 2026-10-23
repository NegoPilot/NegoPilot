"""Live supplier lookup backed by business-mcp (PostgreSQL) for the internal lookup routes."""

import json
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol

from negopilot_agent.routing.mcp_client import DEFAULT_URLS

from .supplier_chat_models import CatalogData
from .supplier_chat_service import SupplierNotFound
from .supplier_lookup_models import (
    LIVE_SUPPLIER_ID_PATTERN, LiveQuoteListData, LiveQuoteSearch,
    LiveSupplierDetail, LiveSupplierListData, LiveSupplierSearch,
)

CATALOG_URI = "business://catalog"
LIVE_SUPPLIER_ID = re.compile(LIVE_SUPPLIER_ID_PATTERN)


class BusinessUnavailable(Exception):
    """business-mcp or its database could not answer."""


class BusinessSession(Protocol):
    async def call_tool(self, tool: str, args: dict[str, Any]) -> Any: ...

    async def read_catalog(self) -> dict[str, Any]: ...


class McpBusinessSession:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def call_tool(self, tool: str, args: dict[str, Any]) -> Any:
        result = await self._client.call_tool(tool, args)
        return result.data if result.data is not None else result.structured_content

    async def read_catalog(self) -> dict[str, Any]:
        contents = await self._client.read_resource(CATALOG_URI)
        return json.loads(contents[0].text)


@asynccontextmanager
async def mcp_session(url: str | None = None) -> AsyncIterator[BusinessSession]:
    from fastmcp import Client   # 테스트(가짜 session)에서는 fastmcp 없이도 돌도록 늦게 import

    env, default = DEFAULT_URLS["business-mcp"]
    async with Client(url or os.getenv(env, default)) as client:
        yield McpBusinessSession(client)


SessionFactory = Callable[[], AbstractAsyncContextManager[BusinessSession]]


def _aggregate_sources(sources: set[str], allowed: set[str]) -> str:
    if not sources <= allowed:
        raise ValueError("Unsupported business data source")
    if not sources:
        return "UNKNOWN"
    return next(iter(sources)) if len(sources) == 1 else "MIXED"


def _provenance(raw: dict[str, Any], financial_sources: set[str], *,
                has_contract: bool, has_quotes: bool) -> dict[str, str]:
    # 기본 정보와 재무의 출처는 독립적이다. 없는 품질 자료의 기본 출처는 집계하지 않는다.
    operations = {raw["certification_source"], raw["capability_source"]}
    if raw["defect_ppm"] is not None or raw["on_time_rate"] is not None:
        operations.add(raw["quality_source"])
    if has_contract or has_quotes:
        operations.add("MOCK")  # 계약과 견적은 현재 수정할 수 없는 합성 자료다.
    return {
        "profile": raw["source"],
        "financials": _aggregate_sources(financial_sources, {"DART", "MOCK"}),
        "operations": _aggregate_sources(operations, {"MOCK", "MANUAL"}),
    }


def _capability(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in ("process", "material", "monthly_capacity")}


def _debt_ratio(row: dict[str, Any]) -> float | None:
    liabilities, equity = row["total_liabilities"], row["total_equity"]
    if liabilities is None or equity is None or equity <= 0:
        return None
    return round(liabilities / equity * 100, 1)


def _quality(raw: dict[str, Any]) -> dict[str, Any]:
    return {"defect_ppm": raw["defect_ppm"],
            "on_time_delivery_rate": None if raw["on_time_rate"] is None else round(raw["on_time_rate"] * 100, 1),
            "audit_grade": raw["audit_grade"], "years_dealing": raw["years_dealing"]}


def _contract(raw: dict[str, Any]) -> dict[str, Any] | None:
    if raw["payment_days"] is None:
        return None
    return {key: raw[key] for key in ("payment_days", "price_linkage", "raw_material_ratio", "auto_renewal")}


def _quote(row: dict[str, Any]) -> dict[str, Any]:
    return {"quote_id": row["quote_id"], "process": row["process"], "material": row["material"],
            "unit_price_krw": row["unit_price"], "quoted_at": row["quoted_at"]}


def _profile(raw: dict[str, Any], financial_sources: set[str], *, has_quotes: bool) -> dict[str, Any]:
    return {
        **{key: raw[key] for key in ("supplier_id", "name", "region", "iatf16949", "iso14001",
                                   "founded_year", "employees", "updated_at")},
        "quality": _quality(raw), "existing_contract": _contract(raw),
        "provenance": _provenance(raw, financial_sources,
                                  has_contract=raw["payment_days"] is not None, has_quotes=has_quotes),
    }


def _detail(raw: dict[str, Any]) -> LiveSupplierDetail:
    return LiveSupplierDetail.model_validate({
        **_profile(raw, {row["source"] for row in raw["financials"]}, has_quotes=bool(raw["recent_quotes"])),
        **{key: raw[key] or None for key in ("corp_code", "stock_code", "induty_code", "biz_no_masked", "created_at")},
        "capabilities": [_capability(row) for row in raw["capabilities"]],
        "financials": [{
            "year": row["bsns_year"], "fs_div": row["fs_div"],
            "revenue_krw": row["revenue"], "operating_profit_krw": row["operating_income"],
            "net_income_krw": row["net_income"], "total_assets_krw": row["total_assets"],
            "total_liabilities_krw": row["total_liabilities"], "total_equity_krw": row["total_equity"],
            "debt_ratio": _debt_ratio(row), "source": row["source"],
        } for row in raw["financials"]],
        "recent_quotes": [_quote(row) for row in raw["recent_quotes"]],
    })


class BusinessSupplierService:
    """Read paginated supplier directory data through business-mcp tools."""

    def __init__(self, session: SessionFactory = mcp_session) -> None:
        self._session = session

    async def _fetch(self, operation: Callable[[BusinessSession], Awaitable[Any]]) -> Any:
        # MCP의 ToolError는 DB 장애도 포함하므로 모두 연결 실패로 본다.
        # 없는 기업이나 알 수 없는 조건은 도구를 부르기 전에 걸러 ToolError가 나지 않게 한다.
        try:
            async with self._session() as business:
                return await operation(business)
        except Exception as exc:
            raise BusinessUnavailable("business-mcp is unavailable") from exc

    async def catalog(self) -> CatalogData:
        raw = await self._fetch(lambda business: business.read_catalog())
        return CatalogData.model_validate({
            "processes": [
                {"process": process, "materials": sorted(materials)}
                for process, materials in sorted(raw["processes"].items()) if materials
            ],
            "regions": sorted(row["region"] for row in raw["regions"]),
        })

    async def search(self, filters: LiveSupplierSearch) -> LiveSupplierListData:
        raw = await self._fetch(lambda business: business.call_tool(
            "list_suppliers_page", filters.model_dump(exclude_none=True)))
        items = [{
            **_profile(row, set(row["financial_sources"]), has_quotes=row["has_quotes"]),
            "matched_capabilities": [_capability(cap) for cap in row["matched_capabilities"]],
            "max_monthly_capacity": row["max_monthly_capacity"],
            "latest_quote": None if row["latest_quote"] is None else _quote(row["latest_quote"]),
        } for row in raw["items"]]
        return LiveSupplierListData.model_validate(raw | {"items": items})

    async def quotes(self, supplier_id: str, filters: LiveQuoteSearch) -> LiveQuoteListData:
        raw = await self._fetch(lambda business: business.call_tool(
            "list_supplier_quotes", {"supplier_id": supplier_id, **filters.model_dump(exclude_none=True)}))
        if not raw["supplier_exists"]:
            raise SupplierNotFound("Supplier does not exist.")
        return LiveQuoteListData.model_validate({
            key: ([_quote(row) for row in value] if key == "items" else value)
            for key, value in raw.items() if key != "supplier_exists"
        })

    async def detail(self, supplier_id: str) -> LiveSupplierDetail:
        # get_supplier는 이름과 숫자만 있는 ID도 받는다. 계약은 정확한 ID만 허용한다.
        if not LIVE_SUPPLIER_ID.fullmatch(supplier_id):
            raise SupplierNotFound("Supplier does not exist.")

        async def fetch(business: BusinessSession) -> dict[str, Any] | None:
            found = await business.call_tool("find_suppliers", {"supplier": supplier_id, "limit": 1})
            if not found["matches"] or found["matches"][0]["supplier_id"] != supplier_id:
                return None
            return await business.call_tool("get_supplier", {"supplier": supplier_id})

        raw = await self._fetch(fetch)
        if raw is None:
            raise SupplierNotFound("Supplier does not exist.")
        return _detail(raw)
