"""Validate actual ASGI responses against the checked-in contracts.

These tests deliberately never use app.openapi(). All response serialization and
HTTP exception handling run through the actual FastAPI application. Requires
jsonschema in the development dependency group.
"""

import asyncio
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import socket
from uuid import uuid4

import httpx2 as httpx
from jsonschema import Draft202012Validator, FormatChecker
import pytest

from negopilot_agent.api.app import create_app
from negopilot_agent.api.business_supplier_service import BusinessSupplierService
from negopilot_agent.api.settings import ApiSettings
from tests.test_supplier_lookup_live import FakeBusiness


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = ROOT / "docs" / "contracts"
V1 = "agent-http.openapi.json"
V2 = "supplier-chat.openapi.json"
LOOKUP_V2 = "supplier-lookup.openapi.json"
CATALOG = "/internal/v1/business/catalog"
SEARCH = "/internal/v1/suppliers"
DETAIL_SCHEMA = SEARCH + "/{supplier_id}"
CHAT = "/internal/v1/chat/responses"
API_KEY = "test-only-openapi-key"
HEADERS = {"X-Internal-Api-Key": API_KEY}
PRIVATE_MARKER = "private-openapi-test-detail"
SUPPLIER_FIXTURE = json.loads((CONTRACT_DIR / "fixtures" / "supplier-chat.json").read_text(encoding="utf-8"))
SUPPLIERS = {item["supplier_id"]: item for item in SUPPLIER_FIXTURE["suppliers"]}
QUESTIONS = SUPPLIER_FIXTURE["supported_queries"]
ALIAS = "이 회사의 재무 상태와 납기 지표를 알려줘"

V2_ROUTES = [
    ("GET", CATALOG, CATALOG),
    ("GET", SEARCH, SEARCH),
    ("GET", SEARCH + "/SUP-0034", DETAIL_SCHEMA),
    ("POST", CHAT, CHAT),
]


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


def app_for(*, mode="stub", service=None):
    return create_app(
        settings=ApiSettings(mode=mode, internal_api_key=API_KEY, request_timeout_seconds=1),
        supplier_chat_service=service,
    )


async def request(app, method, path, *, headers=None, **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://asgi.test",
    ) as client:
        return await client.request(method, path, headers=HEADERS if headers is None else headers, **kwargs)


def chat_payload(*, query=None, selected=None, **overrides):
    return {"request_id": str(uuid4()), "conversation_id": str(uuid4()),
            "query": QUESTIONS[0] if query is None else query, "history": [],
            "context": {"selected_supplier_ids": ["SUP-0034"] if selected is None else selected}} | overrides


def route_arguments(method, path):
    if method == "POST":
        return {"json": chat_payload()}
    if path == SEARCH:
        return {"params": {"process": "다이캐스팅"}}
    return {}


@pytest.mark.parametrize("mode", ["stub", "live"])
async def test_health_matches_fixed_openapi_without_external_dependencies(mode):
    response = await request(app_for(mode=mode), "GET", "/health", headers={})
    assert assert_wire(response, V1, "/health", "GET") == {"status": "ok"}


async def test_catalog_matches_fixed_openapi_and_shared_supplier_fixture():
    data = assert_wire(await request(app_for(), "GET", CATALOG), V2, CATALOG, "GET")["data"]
    expected = {}
    for supplier in SUPPLIERS.values():
        for capability in supplier["capabilities"]:
            expected.setdefault(capability["process"], set()).add(capability["material"])
    assert {item["process"]: set(item["materials"]) for item in data["processes"]} == expected
    assert len(data["processes"]) == len(expected)
    assert set(data["regions"]) == {supplier["region"] for supplier in SUPPLIERS.values()}


@pytest.mark.parametrize(("params", "supplier_ids", "limit"), [
    ({"process": "다이캐스팅"}, ["SUP-0034", "SUP-0041"], 20),
    ({"process": " 다이캐스팅 ", "limit": "1"}, ["SUP-0034"], 1),
    ({"process": "다이캐스팅", "material": "ADC12", "region": "인천"}, ["SUP-0034"], 20),
    ({"process": "프레스", "iatf_required": "false"}, ["SUP-0052"], 20),
    ({"process": "프레스", "iatf_required": "true"}, [], 20),
    ({"process": "없는 공정", "limit": "100"}, [], 100),
])
async def test_supplier_search_matches_fixed_openapi_and_fixture_values(params, supplier_ids, limit):
    response = await request(app_for(), "GET", SEARCH, params=params)
    data = assert_wire(response, V2, SEARCH, "GET")["data"]
    expected = []
    for supplier_id in supplier_ids:
        supplier = SUPPLIERS[supplier_id]
        matched = [cap for cap in supplier["capabilities"] if cap["process"] == params["process"].strip()
                   and ("material" not in params or cap["material"] == params["material"])]
        expected.append({key: supplier[key] for key in (
            "supplier_id", "name", "region", "iatf16949", "iso14001", "provenance",
        )} | {"matched_capabilities": matched})
    assert data == {"items": expected, "returned_count": len(supplier_ids), "limit": limit}


@pytest.mark.parametrize("supplier_id", list(SUPPLIERS))
async def test_supplier_detail_preserves_fixture_nulls_and_values_in_fixed_openapi(supplier_id):
    response = await request(app_for(), "GET", SEARCH + "/" + supplier_id)
    assert assert_wire(response, V2, DETAIL_SCHEMA, "GET")["data"] == SUPPLIERS[supplier_id]


@pytest.mark.parametrize("supplier_id", list(SUPPLIERS))
@pytest.mark.parametrize("query", QUESTIONS + [ALIAS])
async def test_completed_chat_matches_fixed_openapi_and_selected_fixture(supplier_id, query):
    payload = chat_payload(query=query, selected=[supplier_id])
    data = assert_wire(await request(app_for(), "POST", CHAT, json=payload), V2, CHAT)
    assert data["execution_status"] == "COMPLETED"
    assert data["request_id"] == payload["request_id"]
    assert data["conversation_id"] == payload["conversation_id"]
    assert data["resolved_context"] == payload["context"]
    card = data["presentation"]["cards"][0]
    evidence = data["evidence"][0]
    assert card["supplier"] == SUPPLIERS[supplier_id]
    assert evidence["supplier_id"] == supplier_id
    assert card["evidence_ids"] == [evidence["evidence_id"]]


@pytest.mark.parametrize(("selected", "query", "missing"), [
    ([], QUESTIONS[0], "context.selected_supplier_ids"),
    ([], "지원하지 않는 질문", "context.selected_supplier_ids"),
    (["SUP-0034"], "지원하지 않는 질문", "query"),
])
async def test_clarification_chat_matches_fixed_openapi(selected, query, missing):
    payload = chat_payload(query=query, selected=selected)
    data = assert_wire(await request(app_for(), "POST", CHAT, json=payload), V2, CHAT)
    assert data["execution_status"] == "NEEDS_INPUT"
    assert data["request_id"] == payload["request_id"]
    assert data["conversation_id"] == payload["conversation_id"]
    assert data["resolved_context"] == payload["context"]
    assert data["clarification"] == {"missing_fields": [missing],
                                     "question": data["presentation"]["text"], "options": QUESTIONS}


@pytest.mark.parametrize("mutation", ["invalid_uuid", "missing_evidence", "wrong_status", "fabricated_citation"])
async def test_fixed_v2_schema_rejects_invalid_chat_wire_fields(mutation):
    data = assert_wire(await request(app_for(), "POST", CHAT, json=chat_payload()), V2, CHAT)
    invalid = deepcopy(data)
    if mutation == "invalid_uuid":
        invalid["response_id"] = "not-a-uuid"
    elif mutation == "missing_evidence":
        invalid["evidence"] = []
    elif mutation == "wrong_status":
        invalid["presentation"]["status"] = "NEEDS_INPUT"
    else:
        invalid["citations"] = ["fabricated"]
    assert not response_validator(V2, CHAT, "POST", 200).is_valid(invalid)


@pytest.mark.parametrize("params", [
    {}, {"process": ""}, {"process": "프레스", "limit": "101"},
    {"process": "프레스", "iatf_required": "True"}, {"process": "프레스", "unknown": PRIVATE_MARKER},
    [("process", "프레스"), ("process", "프레스")],
])
async def test_search_validation_errors_match_fixed_v2_openapi(params):
    response = await request(app_for(), "GET", SEARCH, params=params)
    assert_error_wire(response, V2, SEARCH, "GET", 400, "VALIDATION_ERROR")


@pytest.mark.parametrize("path", [CATALOG, SEARCH + "/SUP-0034"])
async def test_extra_lookup_parameters_return_a_contract_error(path):
    response = await request(app_for(), "GET", path, params={"unknown": PRIVATE_MARKER})
    assert_error_wire(response, V2, CATALOG if path == CATALOG else DETAIL_SCHEMA,
                      "GET", 400, "VALIDATION_ERROR")


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_unknown_supplier_matches_fixed_v2_error_contract(method):
    payload = chat_payload(selected=["SUP-NOT-FOUND"])
    if method == "POST":
        response = await request(app_for(), method, CHAT, json=payload)
    else:
        response = await request(app_for(), method, SEARCH + "/SUP-NOT-FOUND")
    assert_error_wire(response, V2, CHAT if method == "POST" else DETAIL_SCHEMA,
                      method, 404, "SUPPLIER_NOT_FOUND", payload["request_id"] if method == "POST" else None)


@pytest.mark.parametrize("override", [
    {"query": " "}, {"unknown": PRIVATE_MARKER},
    {"history": [{"role": "user", "content": "test"}] * 21},
    {"context": {"selected_supplier_ids": ["SUP-0034", "SUP-0034"]}},
    {"conversation_id": "invalid"}, {"request_id": "invalid"},
])
async def test_chat_validation_errors_match_fixed_v2_openapi(override):
    payload = chat_payload(**override)
    response = await request(app_for(), "POST", CHAT, json=payload)
    request_id = None if override.get("request_id") == "invalid" else payload["request_id"]
    assert_error_wire(response, V2, CHAT, "POST", 400, "VALIDATION_ERROR", request_id)


@pytest.mark.parametrize(("contract", "path"), [(V2, CHAT)])
async def test_malformed_json_matches_fixed_error_contract(contract, path):
    response = await request(app_for(), "POST", path, content='{"private-openapi-test-detail":',
                             headers=HEADERS | {"Content-Type": "application/json"})
    assert_error_wire(response, contract, path, "POST", 400, "VALIDATION_ERROR")


@pytest.mark.parametrize(("method", "path", "schema_path"), V2_ROUTES)
@pytest.mark.parametrize("headers", [{}, {"X-Internal-Api-Key": "wrong-test-key"}])
async def test_v2_authentication_errors_match_each_fixed_route_schema(method, path, schema_path, headers):
    arguments = route_arguments(method, path)
    response = await request(app_for(), method, path, headers=headers, **arguments)
    request_id = arguments["json"]["request_id"] if method == "POST" else None
    assert_error_wire(response, V2, schema_path, method, 401, "UNAUTHORIZED", request_id)


@pytest.mark.parametrize(("method", "path", "schema_path"), V2_ROUTES)
async def test_v2_live_mode_rejection_matches_fixed_contract_without_model_creation(method, path, schema_path):
    arguments = route_arguments(method, path)
    response = await request(app_for(mode="live"), method, path, **arguments)
    request_id = arguments["json"]["request_id"] if method == "POST" else None
    assert_error_wire(response, V2, schema_path, method, 503, "DEMO_MODE_REQUIRED", request_id)


class FailingSupplierService:
    def __init__(self, failure):
        self.failure = failure

    async def fail(self):
        # A local yield exercises the async endpoint without a clock or socket.
        await asyncio.sleep(0)
        raise self.failure

    async def catalog(self):
        return await self.fail()

    async def search(self, filters):
        return await self.fail()

    async def detail(self, supplier_id):
        return await self.fail()

    async def respond(self, body):
        return await self.fail()


@pytest.mark.parametrize(("method", "path", "schema_path"), V2_ROUTES)
@pytest.mark.parametrize(("status", "code", "retryable", "failure"), [
    (500, "INTERNAL_ERROR", False, RuntimeError(PRIVATE_MARKER)),
    (504, "UPSTREAM_TIMEOUT", True, TimeoutError(PRIVATE_MARKER)),
])
async def test_v2_service_failures_match_each_fixed_route_schema(
    method, path, schema_path, status, code, retryable, failure,
):
    arguments = route_arguments(method, path)
    response = await request(app_for(service=FailingSupplierService(failure)), method, path, **arguments)
    request_id = arguments["json"]["request_id"] if method == "POST" else None
    assert_error_wire(response, V2, schema_path, method, status, code, request_id, retryable)


@pytest.mark.parametrize("mode", ["stub", "live"])
@pytest.mark.parametrize(("path", "params"), [
    ("/internal/v2/business/catalog", None),
    ("/internal/v2/suppliers", {"process": "다이캐스팅", "iso14001_required": "true"}),
    ("/internal/v2/suppliers/SUP-0001", None),
])
async def test_v2_live_lookups_match_fixed_contract(mode, path, params):
    app = create_app(
        settings=ApiSettings(mode=mode, internal_api_key=API_KEY, request_timeout_seconds=1),
        supplier_lookup_service=BusinessSupplierService(session=FakeBusiness().session),
    )
    response = await request(app, "GET", path, params=params)
    schema_path = "/internal/v2/suppliers/{supplier_id}" if path.endswith("SUP-0001") else path
    data = assert_wire(response, LOOKUP_V2, schema_path, "GET")
    assert (data["schema_version"], data["mode"]) == ("0.3", "live")


async def test_v2_live_lookup_errors_match_fixed_contract():
    app = create_app(
        settings=ApiSettings(mode="live", internal_api_key=API_KEY, request_timeout_seconds=1),
        supplier_lookup_service=BusinessSupplierService(session=FakeBusiness(failure=RuntimeError("x")).session),
    )
    response = await request(app, "GET", "/internal/v2/business/catalog")
    assert_error_wire(response, LOOKUP_V2, "/internal/v2/business/catalog", "GET", 503,
                      "UPSTREAM_UNAVAILABLE", retryable=True)
