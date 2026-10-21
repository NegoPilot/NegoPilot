"""Supplier/chat v0.2 contract tests. All external socket connections are blocked."""

import asyncio
import json
import socket
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest

from negopilot_agent.api.app import create_app
from negopilot_agent.api.settings import ApiSettings
from negopilot_agent.api.supplier_chat_models import Context
from negopilot_agent.api.supplier_chat_service import SupplierChatService

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "docs/contracts/fixtures/supplier-chat.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
SUPPLIERS = {supplier["supplier_id"]: supplier for supplier in FIXTURE["suppliers"]}
QUERIES = FIXTURE["supported_queries"]
ALIAS_QUERY = "이 회사의 재무 상태와 납기 지표를 알려줘"
API_KEY = "test-only-supplier-chat-key"
HEADERS = {"X-Internal-Api-Key": API_KEY}
CATALOG = "/internal/v1/business/catalog"
SEARCH = "/internal/v1/suppliers"
DETAIL = SEARCH + "/SUP-0034"
CHAT = "/internal/v1/chat/responses"
ROUTES = [("GET", CATALOG), ("GET", SEARCH), ("GET", DETAIL), ("POST", CHAT)]
INVALID_SEGMENT_IDS = ["SUP 0034", "SUP\t0034", "기업0034", "SUP.0034", "SUP@0034", "SUP?0034"]


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Supplier/chat tests must not contact external services")

    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket.socket, "connect_ex", fail)


def app_for(**overrides):
    return create_app(settings=ApiSettings(**({
        "mode": "stub", "internal_api_key": API_KEY, "request_timeout_seconds": 30.0,
    } | overrides)))


def chat_body(query=QUERIES[0], selected=None, **overrides):
    return {
        "request_id": str(uuid4()), "conversation_id": str(uuid4()), "query": query,
        "history": [], "context": {"selected_supplier_ids": ["SUP-0034"] if selected is None else selected},
    } | overrides


async def request(app, method, path, *, payload=None, params=None, headers=HEADERS):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, json=payload, params=params, headers=headers)


async def chat(app, payload):
    return await request(app, "POST", CHAT, payload=payload)


def assert_error(response, status, code, request_id=None, retryable=False):
    assert response.status_code == status
    data = response.json()
    assert set(data) == {"request_id", "error"}
    assert data["request_id"] == request_id
    assert set(data["error"]) == {"code", "message", "retryable"}
    assert data["error"]["code"] == code
    assert isinstance(data["error"]["message"], str) and data["error"]["message"]
    assert data["error"]["retryable"] is retryable


def lookup_data(response):
    assert response.status_code == 200
    envelope = response.json()
    assert set(envelope) == {"schema_version", "mode", "data"}
    assert envelope["schema_version"] == "0.2"
    assert envelope["mode"] == "stub"
    return envelope["data"]


def assert_chat_base(response, payload):
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "schema_version", "mode", "request_id", "conversation_id", "response_id",
        "execution_status", "generation_status", "resolved_context", "presentation",
        "citations", "evidence", "clarification",
    }
    assert data["schema_version"] == "0.2"
    assert data["mode"] == "stub"
    assert data["request_id"] == payload["request_id"]
    assert data["conversation_id"] == payload["conversation_id"]
    assert str(UUID(data["response_id"])) == data["response_id"]
    assert data["generation_status"] == "NOT_IMPLEMENTED"
    assert data["resolved_context"] == payload["context"]
    assert data["citations"] == []
    assert set(data["presentation"]) == {"status", "renderer", "text", "cards"}
    assert data["presentation"]["renderer"] == "template"
    assert isinstance(data["presentation"]["text"], str) and data["presentation"]["text"].strip()
    return data


def assert_completed(response, payload):
    data = assert_chat_base(response, payload)
    supplier_id = payload["context"]["selected_supplier_ids"][0]
    assert data["execution_status"] == "COMPLETED"
    assert data["presentation"]["status"] == "READY"
    assert data["clarification"] is None
    assert len(data["presentation"]["cards"]) == len(data["evidence"]) == 1
    card = data["presentation"]["cards"][0]
    evidence = data["evidence"][0]
    assert set(card) == {"type", "supplier", "evidence_ids"}
    assert card["type"] == "supplier_detail"
    assert card["supplier"] == SUPPLIERS[supplier_id]
    assert set(evidence) == {"evidence_id", "server", "tool", "supplier_id", "source", "executed"}
    assert isinstance(evidence["evidence_id"], str) and evidence["evidence_id"]
    assert evidence["server"] == "business-mcp"
    assert evidence["tool"] == "get_supplier"
    assert evidence["supplier_id"] == supplier_id
    assert evidence["source"] == "stub"
    assert evidence["executed"] is False
    assert card["evidence_ids"] == [evidence["evidence_id"]]
    return data


def assert_needs_input(response, payload, missing_field):
    data = assert_chat_base(response, payload)
    assert data["execution_status"] == "NEEDS_INPUT"
    assert data["presentation"]["status"] == "NEEDS_INPUT"
    assert data["presentation"]["cards"] == []
    assert data["evidence"] == []
    assert set(data["clarification"]) == {"missing_fields", "question", "options"}
    assert data["clarification"]["missing_fields"] == [missing_field]
    assert data["clarification"]["question"] == data["presentation"]["text"]
    assert data["clarification"]["options"] == QUERIES
    return data


async def test_catalog_is_derived_from_fixture_without_duplicates():
    data = lookup_data(await request(app_for(), "GET", CATALOG))
    assert set(data) == {"processes", "regions"}
    expected = {}
    for supplier in SUPPLIERS.values():
        for capability in supplier["capabilities"]:
            expected.setdefault(capability["process"], set()).add(capability["material"])
    assert len(data["processes"]) == len(expected)
    assert {item["process"]: set(item["materials"]) for item in data["processes"]} == expected
    for item in data["processes"]:
        assert set(item) == {"process", "materials"}
        assert len(item["materials"]) == len(set(item["materials"]))
    assert set(data["regions"]) == {supplier["region"] for supplier in SUPPLIERS.values()}
    assert len(data["regions"]) == len(set(data["regions"]))


@pytest.mark.parametrize(("params", "expected_ids", "limit"), [
    ({"process": "다이캐스팅"}, ["SUP-0034", "SUP-0041"], 20),
    ({"process": "다이캐스팅", "material": "ADC12"}, ["SUP-0034", "SUP-0041"], 20),
    ({"process": "다이캐스팅", "material": "ALDC6"}, ["SUP-0034"], 20),
    ({"process": "다이캐스팅", "region": "부산"}, ["SUP-0041"], 20),
    ({"process": " 다이캐스팅 ", "material": " ADC12 ", "region": " 인천 "}, ["SUP-0034"], 20),
    ({"process": "다이캐스팅", "limit": "1"}, ["SUP-0034"], 1),
    ({"process": "프레스", "iatf_required": "false"}, ["SUP-0052"], 20),
    ({"process": "프레스", "iatf_required": "true"}, [], 20),
    ({"process": "다이캐스팅", "iatf_required": "true", "limit": "100"}, ["SUP-0034", "SUP-0041"], 100),
    ({"process": "다이캐스팅", "iso14001_required": "true"}, ["SUP-0034"], 20),
    ({"process": "다이캐스팅", "iso14001_required": "false"}, ["SUP-0034", "SUP-0041"], 20),
    ({"process": "프레스", "iatf_required": "true", "iso14001_required": "true"}, [], 20),
    ({"process": "없는 공정"}, [], 20),
    ({"process": "다이캐스팅", "material": "없는 소재"}, [], 20),
    ({"process": "다이캐스팅", "region": "없는 지역"}, [], 20),
    ({"process": "다이캐스팅", "min_monthly_capacity": "30000"}, ["SUP-0034", "SUP-0041"], 20),
    ({"process": "다이캐스팅", "min_monthly_capacity": "42000"}, ["SUP-0034", "SUP-0041"], 20),
    ({"process": "다이캐스팅", "min_monthly_capacity": "50000"}, ["SUP-0034"], 20),
    ({"process": "다이캐스팅", "min_monthly_capacity": "60001"}, [], 20),
])
async def test_search_filters_groups_and_limits_suppliers(params, expected_ids, limit):
    data = lookup_data(await request(app_for(), "GET", SEARCH, params=params))
    assert set(data) == {"items", "returned_count", "limit"}
    assert data["returned_count"] == len(expected_ids)
    assert data["limit"] == limit
    assert [item["supplier_id"] for item in data["items"]] == expected_ids
    for item in data["items"]:
        supplier = SUPPLIERS[item["supplier_id"]]
        capabilities = [cap for cap in supplier["capabilities"]
                        if cap["process"] == params["process"].strip()
                        and ("material" not in params or cap["material"] == params["material"].strip())
                        and cap["monthly_capacity"] >= int(params.get("min_monthly_capacity", 0))]
        assert item == {key: supplier[key] for key in
                        ("supplier_id", "name", "region", "iatf16949", "iso14001", "provenance")} | {
            "matched_capabilities": capabilities}


@pytest.mark.parametrize("params", [
    {},
    {"process": ""},
    {"process": " \t"},
    {"process": "가" * 101},
    {"process": "프레스", "material": ""},
    {"process": "프레스", "material": " \t"},
    {"process": "프레스", "material": "가" * 101},
    {"process": "프레스", "region": ""},
    {"process": "프레스", "region": "가" * 101},
    {"process": "프레스", "iatf_required": "1"},
    {"process": "프레스", "iatf_required": "True"},
    {"process": "프레스", "iatf_required": "yes"},
    {"process": "프레스", "iso14001_required": "1"},
    {"process": "프레스", "iso14001_required": "True"},
    {"process": "프레스", "limit": "0"},
    {"process": "프레스", "limit": "101"},
    {"process": "프레스", "limit": "1.5"},
    {"process": "프레스", "limit": "true"},
    {"process": "프레스", "min_monthly_capacity": "0"},
    {"process": "프레스", "min_monthly_capacity": "10000001"},
    {"process": "프레스", "min_monthly_capacity": "-1"},
    {"process": "프레스", "min_monthly_capacity": "1.5"},
    {"process": "프레스", "min_monthly_capacity": ""},
    [("process", "프레스"), ("min_monthly_capacity", "1"), ("min_monthly_capacity", "2")],
    {"process": "프레스", "unknown": "private-query-value"},
    {"process": "프레스", "offset": "0"},
    [("process", "프레스"), ("process", "프레스")],
    [("process", "프레스"), ("limit", "1"), ("limit", "2")],
    [("process", "프레스"), ("material", "SPCC"), ("material", "SPCC")],
    [("process", "프레스"), ("region", "대구"), ("region", "대구")],
    [("process", "프레스"), ("iatf_required", "false"), ("iatf_required", "false")],
])
async def test_search_rejects_invalid_or_duplicate_parameters(params, caplog):
    response = await request(app_for(), "GET", SEARCH, params=params)
    assert_error(response, 400, "VALIDATION_ERROR")
    assert "private-query-value" not in response.text + caplog.text


async def test_search_accepts_string_length_boundary_after_trimming():
    data = lookup_data(await request(app_for(), "GET", SEARCH, params={
        "process": " " + "가" * 100 + " ", "material": "나" * 100, "region": "다" * 100,
    }))
    assert data == {"items": [], "returned_count": 0, "limit": 20}


async def test_duplicate_fixture_rows_are_grouped_before_sorting_and_limit(tmp_path):
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    first = fixture["suppliers"][0]
    fixture["suppliers"] = [
        fixture["suppliers"][1],
        first | {"capabilities": [first["capabilities"][0]]},
        fixture["suppliers"][2],
        first | {"capabilities": first["capabilities"]},
    ]
    path = tmp_path / "duplicate-suppliers.json"
    path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    service = SupplierChatService(fixture_path=path)
    app = create_app(settings=ApiSettings(mode="stub", internal_api_key=API_KEY), supplier_chat_service=service)
    data = lookup_data(await request(app, "GET", SEARCH, params={"process": "다이캐스팅", "limit": "1"}))
    assert data["returned_count"] == data["limit"] == 1
    assert [item["supplier_id"] for item in data["items"]] == ["SUP-0034"]
    assert data["items"][0]["matched_capabilities"] == SUPPLIERS["SUP-0034"]["capabilities"]
    catalog = lookup_data(await request(app, "GET", CATALOG))
    materials = next(item["materials"] for item in catalog["processes"] if item["process"] == "다이캐스팅")
    assert sorted(materials) == ["ADC12", "ALDC6"]


async def test_default_fixture_path_does_not_depend_on_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    data = lookup_data(await request(app_for(), "GET", DETAIL))
    assert data == SUPPLIERS["SUP-0034"]


@pytest.mark.parametrize("supplier_id", list(SUPPLIERS))
async def test_detail_preserves_every_fixture_field_including_null(supplier_id):
    data = lookup_data(await request(app_for(), "GET", SEARCH + "/" + supplier_id))
    assert data == SUPPLIERS[supplier_id]


@pytest.mark.parametrize("supplier_id", ["SUP-9999", "sup-0034", "_a-9", "S" * 100])
async def test_unknown_or_differently_cased_supplier_returns_404(supplier_id):
    response = await request(app_for(), "GET", SEARCH + "/" + supplier_id)
    assert_error(response, 404, "SUPPLIER_NOT_FOUND")


@pytest.mark.parametrize("supplier_id", ["  ", "S" * 101] + INVALID_SEGMENT_IDS)
async def test_detail_rejects_invalid_single_segment_supplier_id(supplier_id):
    response = await request(app_for(), "GET", SEARCH + "/" + quote(supplier_id, safe=""))
    assert_error(response, 400, "VALIDATION_ERROR")


@pytest.mark.parametrize("suffix", ["foo/bar", "foo%2Fbar"])
@pytest.mark.parametrize(("mode", "headers", "status", "code"), [
    ("stub", HEADERS, 400, "VALIDATION_ERROR"),
    ("stub", {}, 401, "UNAUTHORIZED"),
    ("live", HEADERS, 400, "VALIDATION_ERROR"),
])
async def test_slash_supplier_path_preserves_authentication_guard_in_both_modes(suffix, mode, headers, status, code):
    response = await request(app_for(mode=mode), "GET", SEARCH + "/" + suffix, headers=headers)
    assert_error(response, status, code)


@pytest.mark.parametrize("query", QUERIES + [ALIAS_QUERY, "  " + QUERIES[0] + "  "])
@pytest.mark.parametrize("supplier_id", list(SUPPLIERS))
async def test_supported_chat_has_consistent_context_cards_and_evidence(query, supplier_id):
    payload = chat_body(query, selected=[supplier_id])
    assert_completed(await chat(app_for(), payload), payload)


async def test_alias_uses_same_template_and_fixture_as_overview():
    app = app_for()
    overview = chat_body()
    alias = chat_body(ALIAS_QUERY)
    first = assert_completed(await chat(app, overview), overview)
    second = assert_completed(await chat(app, alias), alias)
    assert first["presentation"]["text"] == second["presentation"]["text"]
    assert first["presentation"]["cards"][0]["supplier"] == second["presentation"]["cards"][0]["supplier"]


@pytest.mark.parametrize("query", [QUERIES[0], "지원하지 않는 질문"])
async def test_no_supplier_takes_priority_over_question(query):
    payload = chat_body(query, selected=[])
    assert_needs_input(await chat(app_for(), payload), payload, "context.selected_supplier_ids")


@pytest.mark.parametrize("query", ["오늘 날씨 알려줘", "그럼 어때?", QUERIES[0] + "!", "가" * 4000])
async def test_unsupported_question_needs_input_even_with_history(query):
    payload = chat_body(query, history=[{"role": "user", "content": QUERIES[0]},
                                      {"role": "assistant", "content": "previous test response"}])
    assert_needs_input(await chat(app_for(), payload), payload, "query")


@pytest.mark.parametrize("query", [QUERIES[0], "지원하지 않는 질문"])
@pytest.mark.parametrize("supplier_id", ["SUP-9999", "sup-0034", "_a-9", "S" * 100])
async def test_unknown_chat_supplier_is_404(query, supplier_id):
    payload = chat_body(query, selected=[supplier_id])
    response = await chat(app_for(), payload)
    assert_error(response, 404, "SUPPLIER_NOT_FOUND", payload["request_id"])


@pytest.mark.parametrize("supplier_id", ["SUP/0034"] + INVALID_SEGMENT_IDS)
async def test_chat_context_rejects_non_url_safe_supplier_ids(supplier_id):
    payload = chat_body(selected=[supplier_id])
    response = await chat(app_for(), payload)
    assert_error(response, 400, "VALIDATION_ERROR", payload["request_id"])


async def test_requests_keep_independent_context_and_generate_fresh_response_ids():
    app = app_for()
    first_payload = chat_body(selected=["SUP-0034"])
    second_payload = chat_body(selected=["SUP-0041"])
    first, second = await asyncio.gather(chat(app, first_payload), chat(app, second_payload))
    first_data = assert_completed(first, first_payload)
    second_data = assert_completed(second, second_payload)
    repeated_data = assert_completed(await chat(app, first_payload), first_payload)
    assert len({first_data["response_id"], second_data["response_id"], repeated_data["response_id"]}) == 3
    no_selection = chat_body(selected=[])
    assert_needs_input(await chat(app, no_selection), no_selection, "context.selected_supplier_ids")


async def test_history_limit_and_content_length_boundary_are_accepted():
    payload = chat_body(history=[{"role": "user", "content": "가" * 4000}] * 20)
    assert_completed(await chat(app_for(), payload), payload)


@pytest.mark.parametrize("override", [
    {"query": ""},
    {"query": " \t\n"},
    {"query": "가" * 4001},
    {"query": 1},
    {"query": None},
    {"unknown": "private-body-value"},
    {"history": None},
    {"history": {}},
    {"history": [{"role": "user", "content": "previous"}] * 21},
    {"history": [{"role": "system", "content": "previous"}]},
    {"history": [{"role": "tool", "content": "previous"}]},
    {"history": [{"role": "user", "content": ""}]},
    {"history": [{"role": "user", "content": " \n"}]},
    {"history": [{"role": "user", "content": "가" * 4001}]},
    {"history": [{"role": "user", "content": 1}]},
    {"history": [{"role": "user", "content": "previous", "unknown": "private-history-value"}]},
    {"history": [{"role": "user"}]},
    {"context": None},
    {"context": {}},
    {"context": {"selected_supplier_ids": None}},
    {"context": {"selected_supplier_ids": "SUP-0034"}},
    {"context": {"selected_supplier_ids": ["SUP-0034", "SUP-0041"]}},
    {"context": {"selected_supplier_ids": ["SUP-0034", "SUP-0034"]}},
    {"context": {"selected_supplier_ids": [""]}},
    {"context": {"selected_supplier_ids": [" \t"]}},
    {"context": {"selected_supplier_ids": ["S" * 101]}},
    {"context": {"selected_supplier_ids": [34]}},
    {"context": {"selected_supplier_ids": [], "unknown": "private-context-value"}},
    {"conversation_id": "not-a-uuid"},
    {"conversation_id": uuid4().hex},
    {"conversation_id": 1},
])
async def test_chat_rejects_invalid_or_unknown_fields(override, caplog):
    payload = chat_body(**override)
    response = await chat(app_for(), payload)
    assert_error(response, 400, "VALIDATION_ERROR", payload["request_id"])
    for marker in ("private-body-value", "private-history-value", "private-context-value"):
        assert marker not in response.text + caplog.text


@pytest.mark.parametrize("field", ["request_id", "conversation_id", "query", "history", "context"])
async def test_every_chat_request_field_is_required(field):
    payload = chat_body()
    payload.pop(field)
    response = await chat(app_for(), payload)
    assert_error(response, 400, "VALIDATION_ERROR", payload.get("request_id"))


@pytest.mark.parametrize("request_id", [None, 1, "not-a-uuid"])
async def test_bad_request_id_returns_null_error_id(request_id):
    response = await chat(app_for(), chat_body(request_id=request_id))
    assert_error(response, 400, "VALIDATION_ERROR")


async def test_malformed_chat_json_has_safe_error(caplog):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for()), base_url="http://test") as client:
        response = await client.post(CHAT, content='{"query": "private-invalid-json"',
                                     headers=HEADERS | {"Content-Type": "application/json"})
    assert_error(response, 400, "VALIDATION_ERROR")
    assert "private-invalid-json" not in response.text + caplog.text


@pytest.mark.parametrize(("method", "path"), ROUTES)
@pytest.mark.parametrize("headers", [{}, {"X-Internal-Api-Key": "private-wrong-key"}])
async def test_all_new_routes_require_internal_key(method, path, headers):
    payload = chat_body() if method == "POST" else None
    response = await request(app_for(), method, path, payload=payload,
                             params={"process": "프레스"} if path == SEARCH else None, headers=headers)
    assert_error(response, 401, "UNAUTHORIZED", payload["request_id"] if payload else None)
    assert API_KEY not in response.text
    assert "private-wrong-key" not in response.text


@pytest.mark.parametrize(("method", "path"), [("POST", CHAT)])
async def test_live_mode_rejects_chat_without_external_calls(method, path):
    service = BlockingService(error=AssertionError("Live demo guard must run before the service"))
    app = create_app(settings=ApiSettings(mode="live", internal_api_key=API_KEY),
                     supplier_chat_service=service)
    payload = chat_body() if method == "POST" else None
    response = await request(app, method, path, payload=payload,
                             params={"process": "프레스"} if path == SEARCH else None)
    assert_error(response, 503, "DEMO_MODE_REQUIRED", payload["request_id"] if payload else None)
    assert service.called is False


@pytest.mark.parametrize(("method", "path"), ROUTES)
@pytest.mark.parametrize("mode", ["stub", "live"])
@pytest.mark.parametrize("server_key", ["", " \t", API_KEY])
async def test_authentication_rejects_before_demo_service_and_preserves_chat_request_id(
        method, path, mode, server_key):
    service = BlockingService(error=AssertionError("Unauthenticated requests must not reach the service"))
    app = create_app(settings=ApiSettings(mode=mode, internal_api_key=server_key),
                     supplier_chat_service=service)
    payload = chat_body() if method == "POST" else None
    headers = {"X-Internal-Api-Key": API_KEY} if not server_key.strip() else {}
    response = await request(app, method, path, payload=payload, headers=headers,
                             params={"process": "프레스"} if path == SEARCH else None)
    assert_error(response, 401, "UNAUTHORIZED", payload["request_id"] if payload else None)
    assert service.called is False


class BlockingService:
    def __init__(self, error=None):
        self.called = False
        self.cancelled = False
        self.error = error

    async def run(self):
        self.called = True
        if self.error:
            raise self.error
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def catalog(self):
        return await self.run()

    async def search(self, filters):
        return await self.run()

    async def detail(self, supplier_id):
        return await self.run()

    async def respond(self, body):
        return await self.run()


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_all_new_routes_enforce_request_timeout(method, path):
    service = BlockingService()
    app = create_app(settings=ApiSettings(mode="stub", internal_api_key=API_KEY,
                                        request_timeout_seconds=0.01), supplier_chat_service=service)
    payload = chat_body() if method == "POST" else None
    async with asyncio.timeout(0.5):
        response = await request(app, method, path, payload=payload,
                                 params={"process": "프레스"} if path == SEARCH else None)
    assert_error(response, 504, "UPSTREAM_TIMEOUT", payload["request_id"] if payload else None, True)
    assert service.called and service.cancelled


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_unexpected_service_error_is_safe(method, path, caplog):
    service = BlockingService(error=RuntimeError("private-service-error " + API_KEY))
    app = create_app(settings=ApiSettings(mode="stub", internal_api_key=API_KEY), supplier_chat_service=service)
    payload = chat_body() if method == "POST" else None
    response = await request(app, method, path, payload=payload,
                             params={"process": "프레스"} if path == SEARCH else None)
    assert_error(response, 500, "INTERNAL_ERROR", payload["request_id"] if payload else None)
    assert "private-service-error" not in response.text + caplog.text
    assert API_KEY not in response.text + caplog.text
    assert "Traceback" not in response.text


@pytest.mark.parametrize("corruption", [
    "request_id", "conversation_id", "resolved_context", "card_supplier", "evidence_supplier",
    "evidence_reference", "executed_zero", "executed_true", "presentation_status", "citations",
    "capacity_fraction", "capacity_negative", "delivery_rate_over_100", "defect_ppm_negative",
])
async def test_inconsistent_chat_model_instance_is_rejected_safely(corruption, caplog):
    class CorruptService(SupplierChatService):
        async def respond(self, body):
            if corruption == "resolved_context":
                # Valid on its own but inconsistent with the request at the HTTP boundary.
                different = body.model_copy(update={"context": Context(selected_supplier_ids=["SUP-0041"])})
                return await super().respond(different)
            result = (await super().respond(body)).model_copy(deep=True)
            if corruption in {"request_id", "conversation_id"}:
                return result.model_copy(update={corruption: str(uuid4())})
            if corruption == "card_supplier":
                result.presentation.cards[0].supplier.supplier_id = "SUP-0041"
            elif corruption == "evidence_supplier":
                result.evidence[0].supplier_id = "SUP-0041"
            elif corruption == "evidence_reference":
                result.presentation.cards[0].evidence_ids = ["private-unregistered-evidence"]
            elif corruption == "executed_zero":
                result.evidence[0].executed = 0
            elif corruption == "executed_true":
                result.evidence[0].executed = True
            elif corruption == "presentation_status":
                result.presentation.status = "NEEDS_INPUT"
            elif corruption == "citations":
                result.citations = ["private-fabricated-citation"]
            elif corruption == "capacity_fraction":
                result.presentation.cards[0].supplier.capabilities[0].monthly_capacity = 0.5
            elif corruption == "capacity_negative":
                result.presentation.cards[0].supplier.capabilities[0].monthly_capacity = -1
            elif corruption == "delivery_rate_over_100":
                result.presentation.cards[0].supplier.quality.on_time_delivery_rate = 101
            elif corruption == "defect_ppm_negative":
                result.presentation.cards[0].supplier.quality.defect_ppm = -1
            return result

    app = create_app(settings=ApiSettings(mode="stub", internal_api_key=API_KEY),
                     supplier_chat_service=CorruptService())
    payload = chat_body()
    response = await chat(app, payload)
    assert_error(response, 500, "INTERNAL_ERROR", payload["request_id"])
    assert "private-unregistered-evidence" not in response.text + caplog.text
    assert "private-fabricated-citation" not in response.text + caplog.text
    assert "Traceback" not in response.text


async def test_detail_rejects_valid_model_for_wrong_supplier():
    class WrongDetailService(SupplierChatService):
        async def detail(self, supplier_id):
            return await super().detail("SUP-0041")

    app = create_app(settings=ApiSettings(mode="stub", internal_api_key=API_KEY),
                     supplier_chat_service=WrongDetailService())
    assert_error(await request(app, "GET", DETAIL), 500, "INTERNAL_ERROR")
