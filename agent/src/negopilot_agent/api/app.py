"""ASGI entry point: uvicorn negopilot_agent.api.app:app."""

import hmac
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from negopilot_agent.routing.router import ToolCaller
from negopilot_agent.decomposition.backends import Backend

from .business_supplier_service import BusinessSupplierService
from .generation_api import add_generation_routes
from .admin_law_api import add_admin_law_routes
from .admin_law_refresh import add_refresh_routes
from .generation_execution import ContextDecomposer
from .models import ErrorDetail, ErrorResponse
from .settings import ApiSettings
from .supplier_lookup_api import add_supplier_lookup_routes, invalid_supplier_detail_path


def extract_request_id(body: object) -> UUID | None:
    if not isinstance(body, dict) or not isinstance(body.get("request_id"), str):
        return None
    try:
        return UUID(body["request_id"])
    except (ValueError, TypeError, AttributeError):
        return None


def error_response(status: int, request_id: UUID | None, code: str, message: str,
                   retryable: bool = False) -> JSONResponse:
    content = ErrorResponse(
        request_id=request_id, error=ErrorDetail(code=code, message=message, retryable=retryable)
    ).model_dump(mode="json")
    return JSONResponse(status_code=status, content=content)


class AuthenticationFailed(Exception):
    def __init__(self, request_id: UUID | None) -> None:
        self.request_id = request_id


def create_app(settings: ApiSettings | None = None,
               supplier_lookup_service: BusinessSupplierService | None = None,
               generation_decomposer: ContextDecomposer | None = None,
               generation_caller: ToolCaller | None = None,
               generation_backend: Backend | None = None,
               admin_law_caller: ToolCaller | None = None,
               law_refresh_control=None) -> FastAPI:
    settings = settings or ApiSettings.from_env()
    application = FastAPI(title="NegoPilot internal execution API", version="0.1")

    async def authenticate(request: Request) -> None:
        supplied = request.headers.get("X-Internal-Api-Key", "")
        expected = settings.internal_api_key
        if not expected.strip() or not hmac.compare_digest(supplied.encode(), expected.encode()):
            try:
                request_id = extract_request_id(await request.json())
            except Exception:
                request_id = None
            raise AuthenticationFailed(request_id)

    @application.exception_handler(AuthenticationFailed)
    async def authentication_error(request: Request, exc: AuthenticationFailed) -> JSONResponse:
        return error_response(401, exc.request_id, "UNAUTHORIZED", "Internal API authentication failed.")

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(400, extract_request_id(exc.body), "VALIDATION_ERROR", "Invalid request.")

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        # Encoded slashes become extra path segments before route matching.
        # Reject them without adding a catch-all supplier route.
        if exc.status_code == 404 and invalid_supplier_detail_path(request.url.path):
            try:
                await authenticate(request)
            except AuthenticationFailed as auth_error:
                return await authentication_error(request, auth_error)
            return error_response(400, None, "VALIDATION_ERROR", "Invalid request.")
        # Includes malformed encodings. Never return parser details or submitted input.
        return error_response(exc.status_code, None, "VALIDATION_ERROR", "Invalid HTTP request.")

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        return error_response(500, None, "INTERNAL_ERROR", "Unexpected server error.")

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    add_supplier_lookup_routes(application, settings, authenticate, error_response, service=supplier_lookup_service)
    add_generation_routes(application, settings, authenticate, error_response,
                          decomposer=generation_decomposer, caller=generation_caller,
                          backend=generation_backend)
    add_refresh_routes(application, settings, authenticate, error_response, law_refresh_control)
    add_admin_law_routes(application, settings, authenticate, error_response, admin_law_caller)
    return application


app = create_app()
