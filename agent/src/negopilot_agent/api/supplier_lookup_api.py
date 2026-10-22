"""v2 live supplier lookup routes. Always read business-mcp, independent of AGENT_EXECUTION_MODE."""

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .business_supplier_service import BusinessSupplierService, BusinessUnavailable
from .models import ErrorResponse
from .settings import ApiSettings
from .supplier_chat_models import SUPPLIER_ID_PATTERN, StrictModel, SupplierSearch
from .supplier_chat_service import SupplierNotFound
from .supplier_lookup_models import (
    LiveCatalogResponse, LiveSupplierDetailResponse, LiveSupplierId, LiveSupplierSearchResponse,
)

LOOKUP_V2_PREFIX = "/internal/v2"


def invalid_supplier_detail_path(path: str, prefix: str = LOOKUP_V2_PREFIX + "/suppliers/") -> bool:
    return path.startswith(prefix) and re.fullmatch(SUPPLIER_ID_PATTERN, path[len(prefix):]) is None


def parse_search(request: Request) -> SupplierSearch:
    pairs = request.query_params.multi_items()
    values: dict[str, Any] = dict(pairs)
    if len(pairs) != len(values):
        raise ValueError("Duplicate query parameters")
    if set(values) - {"process", "material", "region", "iatf_required", "iso14001_required",
                      "min_monthly_capacity", "limit"}:
        raise ValueError("Unknown query parameter")
    for field in ("iatf_required", "iso14001_required"):
        if field in values:
            if values[field] not in {"true", "false"}:
                raise ValueError("Boolean query parameter must be true or false")
            values[field] = values[field] == "true"
    for field in ("min_monthly_capacity", "limit"):
        if field in values:
            if not re.fullmatch(r"[0-9]+", values[field]):
                raise ValueError(f"{field} must be an integer")
            values[field] = int(values[field])
    return SupplierSearch.model_validate(values)


def add_supplier_lookup_routes(
    application: FastAPI,
    settings: ApiSettings,
    authenticate: Callable,
    error_response: Callable,
    service: BusinessSupplierService | None = None,
) -> None:
    router = APIRouter(
        prefix=LOOKUP_V2_PREFIX,
        dependencies=[Depends(authenticate)],
        responses={status: {"model": ErrorResponse} for status in (400, 401, 404, 500, 503, 504)},
    )

    def get_service() -> BusinessSupplierService:
        nonlocal service
        if service is None:
            service = BusinessSupplierService()
        return service

    async def run(operation: Callable[[], Awaitable[StrictModel]], response_model: type[StrictModel]) -> JSONResponse:
        try:
            async with asyncio.timeout(settings.request_timeout_seconds):
                result = response_model.model_validate(await operation())
                return JSONResponse(content=result.model_dump(mode="json"))
        except SupplierNotFound:
            return error_response(404, None, "SUPPLIER_NOT_FOUND", "Supplier was not found.")
        except BusinessUnavailable:
            return error_response(503, None, "UPSTREAM_UNAVAILABLE", "Business data service is unavailable.", True)
        except TimeoutError:
            return error_response(504, None, "UPSTREAM_TIMEOUT", "Execution timed out.", True)
        except Exception:
            return error_response(500, None, "INTERNAL_ERROR", "Unexpected server error.")

    @router.get("/business/catalog", response_model=LiveCatalogResponse)
    async def catalog(request: Request) -> JSONResponse:
        if request.query_params:
            return error_response(400, None, "VALIDATION_ERROR", "Invalid request.")

        async def operation() -> LiveCatalogResponse:
            return LiveCatalogResponse(schema_version="0.3", mode="live", data=await get_service().catalog())

        return await run(operation, LiveCatalogResponse)

    @router.get("/suppliers", response_model=LiveSupplierSearchResponse)
    async def search(request: Request) -> JSONResponse:
        try:
            filters = parse_search(request)
        except (ValueError, ValidationError):
            return error_response(400, None, "VALIDATION_ERROR", "Invalid request.")

        async def operation() -> LiveSupplierSearchResponse:
            result = LiveSupplierSearchResponse(
                schema_version="0.3", mode="live", data=await get_service().search(filters)
            )
            if result.data.limit != filters.limit:
                raise ValueError("Response limit does not match request")
            return result

        return await run(operation, LiveSupplierSearchResponse)

    @router.get("/suppliers/{supplier_id}", response_model=LiveSupplierDetailResponse)
    async def detail(supplier_id: LiveSupplierId, request: Request) -> JSONResponse:
        if request.query_params:
            return error_response(400, None, "VALIDATION_ERROR", "Invalid request.")

        async def operation() -> LiveSupplierDetailResponse:
            result = LiveSupplierDetailResponse(
                schema_version="0.3", mode="live", data=await get_service().detail(supplier_id)
            )
            if result.data.supplier_id != supplier_id:
                raise ValueError("Response supplier does not match request")
            return result

        return await run(operation, LiveSupplierDetailResponse)

    application.include_router(router)
