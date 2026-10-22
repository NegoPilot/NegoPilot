"""Live supplier lookup backed by business-mcp (PostgreSQL) for the internal lookup routes."""

import json
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol

from negopilot_agent.routing.mcp_client import DEFAULT_URLS

from .supplier_chat_models import CatalogData, SupplierSearch
from .supplier_chat_service import SupplierNotFound
from .supplier_lookup_models import LIVE_SUPPLIER_ID_PATTERN, LiveSupplierDetail, LiveSupplierListData

CATALOG_URI = "business://catalog"
# business-mcp caps search_suppliers at 100 rows. A full page may hide more matches.
MCP_ROW_LIMIT = 100
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


def _detail(raw: dict[str, Any]) -> LiveSupplierDetail:
    return LiveSupplierDetail.model_validate({
        "supplier_id": raw["supplier_id"],
        "name": raw["name"],
        "region": raw["region"],
        "iatf16949": raw["iatf16949"],
        "iso14001": raw["iso14001"],
        "capabilities": [_capability(row) for row in raw["capabilities"]],
        "quality": {
            "defect_ppm": raw["defect_ppm"],
            # DB는 0..1 비율, 계약은 0..100 백분율이다.
            "on_time_delivery_rate": None if raw["on_time_rate"] is None else round(raw["on_time_rate"] * 100, 1),
        },
        "financials": [{
            "year": row["bsns_year"],
            "revenue_krw": row["revenue"],
            "operating_profit_krw": row["operating_income"],
            "debt_ratio": _debt_ratio(row),
            "source": row["source"],
        } for row in raw["financials"]],
        "recent_quotes": [{
            "process": row["process"],
            "material": row["material"],
            "unit_price_krw": row["unit_price"],
            "quoted_at": row["quoted_at"],
        } for row in raw["recent_quotes"]],
        "existing_contract": None if raw["payment_days"] is None else {
            "payment_days": raw["payment_days"],
            "price_linkage": raw["price_linkage"],
        },
        "provenance": _provenance(raw, {row["source"] for row in raw["financials"]},
                                  has_contract=raw["payment_days"] is not None,
                                  has_quotes=bool(raw["recent_quotes"])),
    })


class BusinessSupplierService:
    """Read suppliers through business-mcp tools. Same result shapes as the v0.2 fixture service."""

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

    async def search(self, filters: SupplierSearch) -> LiveSupplierListData:
        async def fetch(business: BusinessSession) -> list[dict[str, Any]] | None:
            catalog = await business.read_catalog()
            materials = catalog["processes"].get(filters.process, [])
            if filters.material is not None:
                materials = [material for material in materials if material == filters.material]
            if filters.region is not None and filters.region not in {row["region"] for row in catalog["regions"]}:
                materials = []
            args: dict[str, Any] = {"process": filters.process, "iatf_required": filters.iatf_required,
                                    "iso14001_required": filters.iso14001_required, "limit": MCP_ROW_LIMIT}
            if filters.region is not None:
                args["region"] = filters.region
            if filters.min_monthly_capacity is not None:
                args["min_monthly_capacity"] = filters.min_monthly_capacity
            rows = []
            # 소재별로 나눠 부르면 한 기업은 소재마다 최대 한 행이라 100행 제한에 덜 걸린다.
            for material in materials:
                batch = await business.call_tool("search_suppliers", args | {"material": material})
                if len(batch) >= MCP_ROW_LIMIT:
                    return None
                rows.extend(batch)
            return rows

        rows = await self._fetch(fetch)
        if rows is None:
            # 잘린 결과로 supplier_id 순서의 앞부분을 만들면 기업이 빠질 수 있다.
            raise RuntimeError("search_suppliers result may be truncated")
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            # 계약은 정확히 일치하는 값만 허용한다. MCP의 별칭 정규화 결과는 다시 걸러낸다.
            if filters.region is not None and row["region"] != filters.region:
                continue
            summary = grouped.setdefault(row["supplier_id"], {
                "supplier_id": row["supplier_id"],
                "name": row["name"],
                "region": row["region"],
                "iatf16949": row["iatf16949"],
                "iso14001": row["iso14001"],
                "matched_capabilities": [],
                "provenance": _provenance(row, set(row["financial_sources"]),
                                          has_contract=row["has_contract"], has_quotes=row["has_quotes"]),
            })
            summary["matched_capabilities"].append(_capability(row))
        items = []
        for supplier_id in sorted(grouped)[:filters.limit]:
            summary = grouped[supplier_id]
            summary["matched_capabilities"].sort(key=lambda cap: (cap["process"], cap["material"]))
            items.append(summary)
        return LiveSupplierListData.model_validate({
            "items": items,
            "returned_count": len(items),
            "limit": filters.limit,
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
