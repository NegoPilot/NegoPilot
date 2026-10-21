"""v0.2 supplier routes sharing the v0.1 authentication and error envelope.

Lookups read the fixture in stub mode and business-mcp in live mode. Chat is stub-only.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .models import ErrorResponse
from .business_supplier_service import BusinessSupplierService, BusinessUnavailable
from .settings import ApiSettings
from .supplier_chat_models import (
    CatalogResponse, ChatRequest, ChatResponse, StrictModel, SupplierDetailResponse,
    SupplierSearchResponse, SupplierSearch, SupplierId, SUPPLIER_ID_PATTERN,
)
from .supplier_chat_service import SupplierChatService, SupplierNotFound


class DemoModeRequired(Exception):
    pass


def invalid_supplier_detail_path(path: str) -> bool:
    prefix = "/internal/v1/suppliers/"
    return path.startswith(prefix) and re.fullmatch(SUPPLIER_ID_PATTERN, path[len(prefix):]) is None


def parse_search(request: Request) -> SupplierSearch:
    pairs = request.query_params.multi_items()
    values: dict[str, Any] = dict(pairs)
    if len(pairs) != len(values):
        raise ValueError("Duplicate query parameters")
    if set(values) - {"process", "material", "region", "iatf_required", "min_monthly_capacity", "limit"}:
        raise ValueError("Unknown query parameter")
    if "iatf_required" in values:
        if values["iatf_required"] not in {"true", "false"}:
            raise ValueError("Boolean query parameter must be true or false")
        values["iatf_required"] = values["iatf_required"] == "true"
    for field in ("min_monthly_capacity", "limit"):
        if field in values:
            if not re.fullmatch(r"[0-9]+", values[field]):
                raise ValueError(f"{field} must be an integer")
            values[field] = int(values[field])
    return SupplierSearch.model_validate(values)


def add_supplier_chat_routes(
    application: FastAPI,
    settings: ApiSettings,
    authenticate: Callable,
    error_response: Callable,
    extract_request_id: Callable,
    service: SupplierChatService | None = None,
    lookup_service: BusinessSupplierService | None = None,
) -> None:
    async def require_demo() -> None:
        if settings.mode != "stub":
            raise DemoModeRequired()

    @application.exception_handler(DemoModeRequired)
    async def demo_mode_error(request: Request, exc: DemoModeRequired) -> JSONResponse:
        try:
            request_id = extract_request_id(await request.json())
        except Exception:
            request_id = None
        return error_response(503, request_id, "DEMO_MODE_REQUIRED", "This API requires stub mode.")

    router = APIRouter(
        prefix="/internal/v1",
        dependencies=[Depends(authenticate)],
        responses={status: {"model": ErrorResponse} for status in (400, 401, 404, 500, 503, 504)},
    )

    def get_service() -> SupplierChatService:
        nonlocal service
        if service is None:
            service = SupplierChatService()
        return service

    def get_lookup() -> SupplierChatService | BusinessSupplierService:
        nonlocal lookup_service
        if settings.mode == "stub":
            return get_service()
        if lookup_service is None:
            lookup_service = BusinessSupplierService()
        return lookup_service

    async def run(operation: Callable[[], Awaitable[StrictModel]], response_model: type[StrictModel],
                  request_id: str | None = None) -> JSONResponse:
        try:
            async with asyncio.timeout(settings.request_timeout_seconds):
                result = response_model.model_validate(await operation())
                return JSONResponse(content=result.model_dump(mode="json"))
        except SupplierNotFound:
            return error_response(404, request_id, "SUPPLIER_NOT_FOUND", "Supplier was not found.")
        except BusinessUnavailable:
            return error_response(503, request_id, "UPSTREAM_UNAVAILABLE", "Business data service is unavailable.", True)
        except TimeoutError:
            return error_response(504, request_id, "UPSTREAM_TIMEOUT", "Execution timed out.", True)
        except Exception:
            return error_response(500, request_id, "INTERNAL_ERROR", "Unexpected server error.")

    @router.get("/business/catalog", response_model=CatalogResponse)
    async def catalog(request: Request) -> JSONResponse:
        if request.query_params:
            return error_response(400, None, "VALIDATION_ERROR", "Invalid request.")

        async def operation() -> CatalogResponse:
            return CatalogResponse(schema_version="0.2", mode=settings.mode, data=await get_lookup().catalog())

        return await run(operation, CatalogResponse)

    @router.get("/suppliers", response_model=SupplierSearchResponse)
    async def search(request: Request) -> JSONResponse:
        try:
            filters = parse_search(request)
        except (ValueError, ValidationError):
            return error_response(400, None, "VALIDATION_ERROR", "Invalid request.")

        async def operation() -> SupplierSearchResponse:
            result = SupplierSearchResponse(
                schema_version="0.2", mode=settings.mode, data=await get_lookup().search(filters)
            )
            if result.data.limit != filters.limit:
                raise ValueError("Response limit does not match request")
            return result

        return await run(operation, SupplierSearchResponse)

    @router.get("/suppliers/{supplier_id}", response_model=SupplierDetailResponse)
    async def detail(supplier_id: SupplierId, request: Request) -> JSONResponse:
        if request.query_params:
            return error_response(400, None, "VALIDATION_ERROR", "Invalid request.")

        async def operation() -> SupplierDetailResponse:
            result = SupplierDetailResponse(
                schema_version="0.2", mode=settings.mode, data=await get_lookup().detail(supplier_id)
            )
            if result.data.supplier_id != supplier_id:
                raise ValueError("Response supplier does not match request")
            return result

        return await run(operation, SupplierDetailResponse)

    @router.post("/chat/responses", response_model=ChatResponse, dependencies=[Depends(require_demo)])
    async def respond(body: ChatRequest) -> JSONResponse:
        async def operation() -> ChatResponse:
            result = ChatResponse.model_validate(await get_service().respond(body))
            if (result.request_id != body.request_id or result.conversation_id != body.conversation_id
                    or result.resolved_context != body.context):
                raise ValueError("Response IDs or context do not match request")
            return result

        return await run(operation, ChatResponse, body.request_id)

    application.include_router(router)
