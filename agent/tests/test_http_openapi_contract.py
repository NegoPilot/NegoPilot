"""Validate actual ASGI responses against the checked-in contracts.

These tests deliberately never use app.openapi(). All response serialization and
HTTP exception handling run through the actual FastAPI application. Requires
jsonschema in the development dependency group.
"""

from functools import lru_cache
import json
from pathlib import Path
import socket

import httpx2 as httpx
from jsonschema import Draft202012Validator, FormatChecker
import pytest

from negopilot_agent.api.app import create_app
from negopilot_agent.api.business_supplier_service import BusinessSupplierService
from negopilot_agent.api.settings import ApiSettings
from tests.test_supplier_lookup_live import FakeBusiness


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = ROOT / "docs" / "contracts"
GENERATION = "agent-generation.openapi.json"
LOOKUP = "supplier-lookup.openapi.json"
API_KEY = "test-only-openapi-key"
HEADERS = {"X-Internal-Api-Key": API_KEY}
PRIVATE_MARKER = "private-openapi-test-detail"


@pytest.fixture(autouse=True)
def forbid_external_calls(monkeypatch):
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("OpenAPI tests must not contact a model or external service")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    yield
    # Application error handlers must not turn an attempted connection into a
    # passing test merely because that case happens to expect HTTP 500.
    assert not attempts, "An external connection or model factory was attempted"


@lru_cache(maxsize=None)
def response_validator(contract_name, path, method, status):
    contract = json.loads((CONTRACT_DIR / contract_name).read_text(encoding="utf-8"))
    assert contract["openapi"] == "3.1.0"
    schema = contract["paths"][path][method.lower()]["responses"][str(status)]["content"]["application/json"]["schema"]
    # All references in these fixed contracts are local. Keeping components at
    # the root resolves them without fetching a schema or regenerating one.
    root = {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "components": contract["components"], **schema}
    Draft202012Validator.check_schema(root)
    return Draft202012Validator(root, format_checker=FormatChecker())


def assert_wire(response, contract, path, method="POST", status=200):
    assert response.status_code == status
    assert response.headers["content-type"].split(";")[0] == "application/json"
    data = response.json()
    validator = response_validator(contract, path, method, status)
    errors = list(validator.iter_errors(data))
    # Report locations rather than exception messages that can include payloads.
    assert not errors, "\n".join(
        f"{error.json_path}: schema {'/'.join(map(str, error.absolute_schema_path))}"
        for error in errors
    )
    assert API_KEY not in response.text
    return data


def assert_error_wire(response, contract, path, method, status, code, request_id=None, retryable=False):
    data = assert_wire(response, contract, path, method, status)
    assert data["request_id"] == request_id
    assert data["error"]["code"] == code
    assert data["error"]["retryable"] is retryable
    assert data["error"]["message"].strip()
    assert PRIVATE_MARKER not in response.text
    return data


def app_for(*, mode="stub"):
    return create_app(settings=ApiSettings(mode=mode, internal_api_key=API_KEY, request_timeout_seconds=1))


async def request(app, method, path, *, headers=None, **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://asgi.test",
    ) as client:
        return await client.request(method, path, headers=HEADERS if headers is None else headers, **kwargs)


@pytest.mark.parametrize("mode", ["stub", "live"])
async def test_health_matches_fixed_openapi_without_external_dependencies(mode):
    response = await request(app_for(mode=mode), "GET", "/health", headers={})
    assert assert_wire(response, GENERATION, "/health", "GET") == {"status": "ok"}


@pytest.mark.parametrize("mode", ["stub", "live"])
@pytest.mark.parametrize(("path", "params"), [
    ("/internal/business/catalog", None),
    ("/internal/suppliers", {"process": "다이캐스팅", "iso14001_required": "true"}),
    ("/internal/suppliers/SUP-0001", None),
])
async def test_v2_live_lookups_match_fixed_contract(mode, path, params):
    app = create_app(
        settings=ApiSettings(mode=mode, internal_api_key=API_KEY, request_timeout_seconds=1),
        supplier_lookup_service=BusinessSupplierService(session=FakeBusiness().session),
    )
    response = await request(app, "GET", path, params=params)
    schema_path = "/internal/suppliers/{supplier_id}" if path.endswith("SUP-0001") else path
    data = assert_wire(response, LOOKUP, schema_path, "GET")
    assert (data["schema_version"], data["mode"]) == ("0.3", "live")


async def test_v2_live_lookup_errors_match_fixed_contract():
    app = create_app(
        settings=ApiSettings(mode="live", internal_api_key=API_KEY, request_timeout_seconds=1),
        supplier_lookup_service=BusinessSupplierService(session=FakeBusiness(failure=RuntimeError("x")).session),
    )
    response = await request(app, "GET", "/internal/business/catalog")
    assert_error_wire(response, LOOKUP, "/internal/business/catalog", "GET", 503,
                      "UPSTREAM_UNAVAILABLE", retryable=True)
