"""Internal HTTP contract tests using ASGI transport and the real Router.

No model or MCP server is contacted. Injected collaborators exercise live wiring
without requiring credentials. Stub tests use the repository's existing fixtures.
"""

import asyncio
from copy import deepcopy
import json
import socket
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx2 as httpx
import pytest

from negopilot_shared import DecompositionResult, RouteResult

from negopilot_agent.api.app import create_app
from negopilot_agent.api import execution
from negopilot_agent.api.settings import ApiSettings

ENDPOINT = "/internal/v1/query-executions"
API_KEY = "test-only-internal-key"
HEADERS = {"X-Internal-Api-Key": API_KEY}
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "shared" / "fixtures" / "decomposition"
ROUTING_FIXTURE_DIR = FIXTURE_DIR.parent / "routing"


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("HTTP contract tests must not contact external services")

    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket.socket, "connect_ex", fail)


def fixture_result(name):
    data = json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return DecompositionResult.model_validate(data["expected"])


def decomposition(*sub_queries):
    base = {"question": "test question", "original_text": "test question",
            "grounding": "required", "confidence": 0.9}
    return DecompositionResult.model_validate({
        "status": "ok", "original_query": "test question",
        "sub_queries": [base | query for query in sub_queries],
    })


class FakeDecomposer:
    def __init__(self, result=None, error=None, delay=0):
        self.result = result or fixture_result("01_law_article")
        self.error = error
        self.delay = delay
        self.calls = []
        self.cancelled = False

    async def decompose(self, query, history=None):
        self.calls.append((query, history))
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.error:
            raise self.error
        return self.result.model_copy(update={"original_query": query}, deep=True)


class FakeCaller:
    def __init__(self, overrides=None, delay=0):
        self.overrides = overrides or {}
        self.delay = delay
        self.calls = []
        self.cancelled = False

    async def __call__(self, server, tool, args):
        self.calls.append((server, tool, args))
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        result = self.overrides.get(tool, {"test_data": True})
        if isinstance(result, Exception):
            raise result
        return result


def settings(**overrides):
    return ApiSettings(**({"mode": "stub", "internal_api_key": API_KEY,
                           "request_timeout_seconds": 30.0} | overrides))


def body(query="하도급법 제4조 보여줘", **overrides):
    return {"request_id": str(uuid4()), "query": query} | overrides


async def post(app, payload, headers=HEADERS):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://test") as client:
        return await client.post(ENDPOINT, json=payload, headers=headers)


def assert_error(response, status, code, request_id, retryable):
    assert response.status_code == status
    data = response.json()
    assert set(data) == {"request_id", "error"}
    assert data["request_id"] == request_id
    assert set(data["error"]) == {"code", "message", "retryable"}
    assert data["error"]["code"] == code
    assert isinstance(data["error"]["message"], str) and data["error"]["message"]
    assert data["error"]["retryable"] is retryable


@pytest.mark.parametrize("mode", ["live", "stub"])
async def test_health_is_available_without_key_or_upstream(mode):
    app = create_app(settings=settings(mode=mode, internal_api_key=""))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(("fixture_name", "execution_status", "error_code"), [
    ("01_law_article", "COMPLETED", None),
    ("09_clarify", "NEEDS_INPUT", None),
    ("11_failed", "FAILED", "DECOMPOSITION_FAILED"),
])
async def test_stub_fixtures(fixture_name, execution_status, error_code):
    expected = fixture_result(fixture_name)
    payload = body(expected.original_query)
    response = await post(create_app(settings=settings()), payload)
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"schema_version", "request_id", "execution_status",
                         "generation_status", "mode", "decomposition", "routing", "error"}
    assert data["schema_version"] == "0.1"
    assert data["request_id"] == payload["request_id"]
    assert data["execution_status"] == execution_status
    assert data["generation_status"] == "NOT_IMPLEMENTED"
    assert data["mode"] == "stub"
    assert data["routing"]["schema_version"] == "0.1"
    assert data["decomposition"] == expected.model_dump(mode="json", by_alias=True)
    if error_code:
        assert data["error"]["code"] == error_code
        assert data["error"]["retryable"] is False
    else:
        assert data["error"] is None
    if fixture_name == "01_law_article":
        assert data["routing"]["status"] == "routed"
        outcome = data["routing"]["outcomes"][0]
        assert outcome["status"] == "ok"
        assert outcome["referenced_articles"] == []
        call = outcome["calls"][0]
        assert call["server"] == "law-mcp"
        assert call["tool"] == "get_article"
        assert call["args"] == {"law": "subcontract_act", "article_no": "제4조"}
        assert call["result"] is not None
        serialized = json.dumps(call["result"], ensure_ascii=False).lower()
        assert any(marker in serialized for marker in ("stub", "test", "테스트"))
    else:
        assert data["routing"]["status"] == "not_routed"
        assert data["routing"]["outcomes"] == []


async def test_stub_rejects_unsupported_question():
    payload = body("지원하지 않는 테스트 질문")
    response = await post(create_app(settings=settings()), payload)
    assert_error(response, 400, "VALIDATION_ERROR", payload["request_id"], False)


@pytest.mark.parametrize("headers", [{}, {"X-Internal-Api-Key": "wrong-key"}])
async def test_missing_or_wrong_key_is_unauthorized(headers):
    payload = body()
    response = await post(create_app(settings=settings()), payload, headers=headers)
    assert_error(response, 401, "UNAUTHORIZED", payload["request_id"], False)
    assert API_KEY not in response.text
    assert "wrong-key" not in response.text


async def test_unconfigured_server_key_never_authenticates():
    payload = body()
    response = await post(create_app(settings=settings(internal_api_key="")), payload)
    assert_error(response, 401, "UNAUTHORIZED", payload["request_id"], False)


@pytest.mark.parametrize("override", [
    {"query": ""},
    {"query": " \t\n "},
    {"query": "가" * 4001},
    {"query": None},
    {"query": 123},
    {"query": ["question"]},
    {"unexpected": "private-field-value"},
    {"history": None},
    {"history": {}},
    {"history": [{"role": "user", "content": "previous"}] * 21},
    {"history": [{"role": "system", "content": "previous"}]},
    {"history": [{"role": "tool", "content": "previous"}]},
    {"history": [{"role": "user", "content": ""}]},
    {"history": [{"role": "assistant", "content": " \n"}]},
    {"history": [{"role": "assistant", "content": "가" * 4001}]},
    {"history": [{"role": "assistant", "content": 123}]},
    {"history": [{"role": "assistant"}]},
    {"history": [{"content": "previous"}]},
    {"history": [{"role": "user", "content": "previous", "name": "private-name"}]},
])
async def test_invalid_request_is_400_and_preserves_request_id(override, caplog):
    payload = body(**override)
    response = await post(create_app(settings=settings()), payload)
    assert_error(response, 400, "VALIDATION_ERROR", payload["request_id"], False)
    assert "private-field-value" not in response.text + caplog.text
    assert "private-name" not in response.text + caplog.text


@pytest.mark.parametrize("payload", [
    {"query": "question"},
    {"request_id": "not-a-uuid", "query": "question"},
    {"request_id": None, "query": "question"},
    [],
])
async def test_invalid_request_id_is_reported_as_null(payload):
    response = await post(create_app(settings=settings()), payload)
    assert_error(response, 400, "VALIDATION_ERROR", None, False)


async def test_malformed_json_does_not_echo_request_or_stack(caplog):
    app = create_app(settings=settings())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://test") as client:
        response = await client.post(ENDPOINT, content='{"query": "private-body-marker"',
                                     headers=HEADERS | {"Content-Type": "application/json"})
    assert_error(response, 400, "VALIDATION_ERROR", None, False)
    assert "private-body-marker" not in response.text + caplog.text
    assert "Traceback" not in response.text


async def test_history_is_forwarded_and_is_not_reused_between_requests():
    decomposer = FakeDecomposer()
    caller = FakeCaller()
    app = create_app(settings=settings(mode="live"), decomposer=decomposer, caller=caller)
    history = [{"role": "user", "content": "질문"},
               {"role": "assistant", "content": "답변"}]
    first = await post(app, body(history=history))
    second = await post(app, body())
    assert first.status_code == second.status_code == 200
    assert decomposer.calls == [("하도급법 제4조 보여줘", history), ("하도급법 제4조 보여줘", [])]
    assert first.json()["mode"] == second.json()["mode"] == "live"
    assert caller.calls == [("law-mcp", "get_article", {"law": "subcontract_act", "article_no": "제4조"})] * 2


async def test_maximum_query_and_history_lengths_are_accepted():
    decomposer = FakeDecomposer()
    app = create_app(settings=settings(mode="live"), decomposer=decomposer, caller=FakeCaller())
    history = [{"role": "user", "content": "가" * 4000}] * 20
    response = await post(app, body("나" * 4000, history=history))
    assert response.status_code == 200
    assert decomposer.calls == [("나" * 4000, history)]


async def test_routing_error_preserves_partial_results_and_hides_exception(caplog):
    result = decomposition(
        {"id": "q1", "intent": "rfq_lookup"},
        {"id": "q2", "intent": "law_article",
         "entities": {"law": "subcontract_act", "article_no": "제4조"}},
        {"id": "q3", "intent": "supplier_ranking"},
    )
    caller = FakeCaller({"list_rfqs": RuntimeError("private-tool-detail " + API_KEY),
                         "get_article": {"test_data": "preserved"}})
    app = create_app(settings=settings(mode="live"), decomposer=FakeDecomposer(result), caller=caller)
    response = await post(app, body())
    assert response.status_code == 200
    data = response.json()
    assert data["execution_status"] == "FAILED"
    assert data["generation_status"] == "NOT_IMPLEMENTED"
    assert data["error"]["code"] == "ROUTING_FAILED"
    assert data["error"]["retryable"] is False
    assert [outcome["status"] for outcome in data["routing"]["outcomes"]] == ["error", "ok", "missing_args"]
    assert data["routing"]["outcomes"][1]["calls"][0]["result"] == {"test_data": "preserved"}
    assert "private-tool-detail" not in response.text + caplog.text
    assert API_KEY not in response.text + caplog.text


async def test_missing_tool_arguments_needs_input_without_call():
    caller = FakeCaller()
    app = create_app(settings=settings(mode="live"), caller=caller,
                     decomposer=FakeDecomposer(decomposition({"id": "q1", "intent": "supplier_ranking"})))
    response = await post(app, body())
    assert response.status_code == 200
    data = response.json()
    assert data["execution_status"] == "NEEDS_INPUT"
    assert data["error"] is None
    assert data["routing"]["outcomes"][0]["status"] == "missing_args"
    assert data["routing"]["outcomes"][0]["missing"] == ["rfq_id"]
    assert caller.calls == []


@pytest.mark.parametrize(("intent", "grounding", "status"), [
    ("general_knowledge", "none", "general_knowledge"),
    ("out_of_scope", "none", "out_of_scope"),
    ("draft_email", "required", "unsupported"),
])
async def test_non_tool_outcomes_are_completed_without_generation(intent, grounding, status):
    caller = FakeCaller()
    result = decomposition({"id": "q1", "intent": intent, "grounding": grounding})
    app = create_app(settings=settings(mode="live"), decomposer=FakeDecomposer(result), caller=caller)
    response = await post(app, body())
    assert response.status_code == 200
    data = response.json()
    assert data["execution_status"] == "COMPLETED"
    assert data["generation_status"] == "NOT_IMPLEMENTED"
    assert data["routing"]["outcomes"][0]["status"] == status
    assert data["routing"]["outcomes"][0]["referenced_articles"] == []
    assert data["error"] is None
    assert caller.calls == []


async def test_empty_result_keeps_alias_and_skipped_outcomes():
    result = decomposition(
        {"id": "q1", "intent": "rfq_lookup"},
        {"id": "q2", "intent": "supplier_ranking", "depends_on": ["q1"],
         "inputs": [{"slot": "rfq_id", "from": "q1", "select": "top1"}]},
        {"id": "q3", "intent": "supplier_detail", "depends_on": ["q2"],
         "inputs": [{"slot": "supplier", "from": "q2", "select": "top1"}]},
    )
    caller = FakeCaller({"list_rfqs": []})
    app = create_app(settings=settings(mode="live"), decomposer=FakeDecomposer(result), caller=caller)
    response = await post(app, body())
    assert response.status_code == 200
    data = response.json()
    assert data["execution_status"] == "COMPLETED"
    assert data["generation_status"] == "NOT_IMPLEMENTED"
    assert [outcome["status"] for outcome in data["routing"]["outcomes"]] == ["empty", "skipped", "skipped"]
    assert data["routing"]["halted"] is False
    assert data["decomposition"]["sub_queries"][1]["inputs"][0] == {
        "slot": "rfq_id", "from": "q1", "select": "top1"}
    assert len(caller.calls) == 1


@pytest.mark.parametrize("stage", ["decomposition", "routing"])
async def test_request_timeout_cancels_current_stage(stage):
    decomposer = FakeDecomposer(delay=1 if stage == "decomposition" else 0)
    caller = FakeCaller(delay=1 if stage == "routing" else 0)
    app = create_app(settings=settings(mode="live", request_timeout_seconds=0.01),
                     decomposer=decomposer, caller=caller)
    payload = body()
    response = await post(app, payload)
    assert_error(response, 504, "UPSTREAM_TIMEOUT", payload["request_id"], True)
    assert decomposer.cancelled if stage == "decomposition" else caller.cancelled


async def test_owned_client_cleanup_is_bounded_after_execution_timeout(monkeypatch):
    model_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    original_close = model_client.aclose
    cleanup_started = asyncio.Event()
    cleanup_cancelled = asyncio.Event()

    async def delayed_close():
        cleanup_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_cancelled.set()
            raise

    monkeypatch.setattr(model_client, "aclose", delayed_close)
    decomposer = FakeDecomposer(delay=10)
    decomposer.backend = SimpleNamespace(http=model_client)
    monkeypatch.setattr(execution, "make_live_decomposer", lambda: decomposer)
    caller = FakeCaller()
    app = create_app(settings=settings(mode="live", request_timeout_seconds=0.01), caller=caller)
    payload = body()
    try:
        # A stalled cleanup must not hold the 504 response indefinitely.
        async with asyncio.timeout(0.5):
            response = await post(app, payload)
        assert_error(response, 504, "UPSTREAM_TIMEOUT", payload["request_id"], True)
        assert decomposer.cancelled
        assert cleanup_started.is_set()
        assert cleanup_cancelled.is_set()
        assert caller.calls == []
    finally:
        await original_close()


@pytest.mark.parametrize("environment", [
    {"NEGOPILOT_DECOMPOSER_BACKEND": "private-invalid-backend"},
    {"NEGOPILOT_DECOMPOSER_BACKEND": "openai", "NEGOPILOT_DECOMPOSER_MODEL": ""},
    {"NEGOPILOT_DECOMPOSER_BACKEND": "openai", "NEGOPILOT_DECOMPOSER_MODEL": " \t"},
])
async def test_invalid_live_model_configuration_returns_safe_503(monkeypatch, caplog, environment):
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    caller = FakeCaller()
    app = create_app(settings=settings(mode="live"), caller=caller)
    payload = body()
    response = await post(app, payload)
    assert_error(response, 503, "UPSTREAM_UNAVAILABLE", payload["request_id"], True)
    assert "private-invalid-backend" not in response.text + caplog.text
    assert caller.calls == []


async def test_model_connection_failure_is_safe_and_retryable(caplog):
    decomposer = FakeDecomposer(error=httpx.ConnectError("private-model-detail " + API_KEY))
    app = create_app(settings=settings(mode="live"), decomposer=decomposer, caller=FakeCaller())
    payload = body()
    response = await post(app, payload)
    assert_error(response, 503, "UPSTREAM_UNAVAILABLE", payload["request_id"], True)
    assert "private-model-detail" not in response.text + caplog.text
    assert API_KEY not in response.text + caplog.text


async def test_invalid_model_url_returns_safe_503(caplog):
    decomposer = FakeDecomposer(error=httpx.InvalidURL("private-model-url " + API_KEY))
    app = create_app(settings=settings(mode="live"), decomposer=decomposer, caller=FakeCaller())
    payload = body()
    response = await post(app, payload)
    assert_error(response, 503, "UPSTREAM_UNAVAILABLE", payload["request_id"], True)
    assert "private-model-url" not in response.text + caplog.text
    assert API_KEY not in response.text + caplog.text


async def test_upstream_timeout_maps_to_504():
    decomposer = FakeDecomposer(error=httpx.ReadTimeout("private-timeout-detail"))
    app = create_app(settings=settings(mode="live"), decomposer=decomposer, caller=FakeCaller())
    payload = body()
    response = await post(app, payload)
    assert_error(response, 504, "UPSTREAM_TIMEOUT", payload["request_id"], True)
    assert "private-timeout-detail" not in response.text


async def test_unexpected_failure_is_safe(caplog):
    decomposer = FakeDecomposer(error=RuntimeError("private-internal-detail " + API_KEY))
    app = create_app(settings=settings(mode="live"), decomposer=decomposer, caller=FakeCaller())
    payload = body()
    response = await post(app, payload)
    assert_error(response, 500, "INTERNAL_ERROR", payload["request_id"], False)
    assert "private-internal-detail" not in response.text + caplog.text
    assert API_KEY not in response.text + caplog.text
    assert "Traceback" not in response.text


# Expectations come from the checked-in contract fixtures. In particular 02, 03
# and 08 record successful outcomes and must not be reclassified as failures.
RECORDED_EXECUTIONS = [
    ("01_law_article", "COMPLETED", ["ok"]),
    ("02_parallel_suppliers", "COMPLETED", ["ok", "ok"]),
    ("03_rank_then_law", "COMPLETED", ["ok", "ok"]),
    ("04_business_wording", "COMPLETED", ["ok"]),
    ("05_offer_check", "COMPLETED", ["ok"]),
    ("06_general_knowledge", "COMPLETED", ["general_knowledge"]),
    ("07_number_needs_grounding", "COMPLETED", ["ok"]),
    ("08_assumption_instead_of_clarify", "COMPLETED", ["ok", "ok"]),
    ("09_clarify", "NEEDS_INPUT", []),
    ("10_out_of_scope", "COMPLETED", ["out_of_scope"]),
    ("11_failed", "FAILED", []),
]


def recorded_routing(name):
    return json.loads((ROUTING_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


class RecordedToolCaller:
    """Replay only calls explicitly recorded in a shared fixture."""

    def __init__(self, routing, *, reference_error=None):
        self.remaining = deepcopy([
            call for outcome in routing["outcomes"]
            for call in outcome["calls"] + outcome["referenced_articles"]
        ])
        self.reference_error = reference_error
        self.calls = []

    async def __call__(self, server, tool, args):
        self.calls.append((server, tool, deepcopy(args)))
        for index, recorded in enumerate(self.remaining):
            if (recorded["server"], recorded["tool"], recorded["args"]) == (server, tool, args):
                self.remaining.pop(index)
                if self.reference_error is not None and tool == "get_article":
                    raise RuntimeError(self.reference_error)
                assert recorded["error"] is None
                return deepcopy(recorded["result"])
        raise AssertionError(f"Unrecorded test tool call: {server}/{tool}")


@pytest.mark.parametrize(("name", "expected_status", "outcome_statuses"), RECORDED_EXECUTIONS)
async def test_latest_shared_fixtures_replay_through_real_router(name, expected_status, outcome_statuses):
    recorded = recorded_routing(name)
    assert recorded["decomposition"] == f"decomposition/{name}.json"
    expected_decomposition = fixture_result(name)
    caller = RecordedToolCaller(recorded["routing"])
    app = create_app(settings=settings(mode="live"),
                     decomposer=FakeDecomposer(expected_decomposition), caller=caller)
    payload = body(recorded["query"])
    response = await post(app, payload)
    assert response.status_code == 200
    data = response.json()
    assert data["request_id"] == payload["request_id"]
    assert data["execution_status"] == expected_status
    assert data["generation_status"] == "NOT_IMPLEMENTED"
    assert data["routing"] == recorded["routing"]
    assert data["routing"]["schema_version"] == "0.1"
    assert [item["status"] for item in data["routing"]["outcomes"]] == outcome_statuses
    assert data["decomposition"] == expected_decomposition.model_dump(mode="json", by_alias=True)
    assert caller.remaining == []
    if name == "11_failed":
        assert data["error"] == {
            "code": "DECOMPOSITION_FAILED", "message": "Query decomposition failed.", "retryable": False,
        }
    else:
        assert data["error"] is None


@pytest.mark.parametrize("private_error", ["", "private-reference-detail", API_KEY])
async def test_reference_failure_keeps_completed_main_outcome_and_preserves_law_refs(private_error, caplog):
    recorded = recorded_routing("05_offer_check")
    caller = RecordedToolCaller(recorded["routing"], reference_error=private_error)
    app = create_app(settings=settings(mode="live"),
                     decomposer=FakeDecomposer(fixture_result("05_offer_check")), caller=caller)
    response = await post(app, body(recorded["query"]))
    assert response.status_code == 200
    data = response.json()
    assert data["execution_status"] == "COMPLETED"
    assert data["error"] is None
    outcome = data["routing"]["outcomes"][0]
    expected = recorded["routing"]["outcomes"][0]
    assert outcome["status"] == "ok"
    assert outcome["message"] is None
    assert outcome["calls"] == expected["calls"]
    assert len(outcome["referenced_articles"]) == 3
    for actual, original in zip(outcome["referenced_articles"], expected["referenced_articles"], strict=True):
        assert actual == original | {"result": None, "error": "Tool execution failed."}
    assert caller.remaining == []
    if private_error:
        assert private_error not in response.text + caplog.text


def app_with_recorded_router(monkeypatch, name, routing):
    """Inject recorded core output to isolate the HTTP copy/sanitization boundary."""
    class RecordedRouter:
        def __init__(self, caller):
            pass

        async def route(self, decomposition):
            return routing

    monkeypatch.setattr(execution, "Router", RecordedRouter)
    return create_app(settings=settings(mode="live"),
                      decomposer=FakeDecomposer(fixture_result(name)), caller=FakeCaller())


@pytest.mark.parametrize("raw_error", [None, "", "private-original-error"])
async def test_http_sanitizes_every_non_null_error_and_message_without_mutating_core(
        monkeypatch, raw_error, caplog):
    recorded = recorded_routing("05_offer_check")
    routing_data = deepcopy(recorded["routing"])
    outcome = routing_data["outcomes"][0]
    outcome["message"] = raw_error
    outcome["calls"][0]["error"] = raw_error
    outcome["calls"][0]["result"] = {
        "message": raw_error, "error": raw_error, "nested": [None, True, 3.25, {"text": "business data"}],
    }
    if raw_error is not None:
        outcome["status"] = "error"
    for reference in outcome["referenced_articles"]:
        reference["error"] = raw_error
        reference["result"] = {"citation": "recorded citation", "error": raw_error}
    core_result = RouteResult.model_validate(routing_data)
    before = core_result.model_dump(mode="json")
    response = await post(app_with_recorded_router(monkeypatch, "05_offer_check", core_result),
                          body(recorded["query"]))
    assert response.status_code == 200
    data = response.json()
    public = data["routing"]["outcomes"][0]
    expected = deepcopy(before)
    safe = expected["outcomes"][0]
    safe["message"] = None if raw_error is None else "Routing details are unavailable."
    for call in safe["calls"] + safe["referenced_articles"]:
        call["error"] = None if raw_error is None else "Tool execution failed."
    assert data["routing"] == expected
    assert public["calls"][0]["result"] == outcome["calls"][0]["result"]
    assert core_result.model_dump(mode="json") == before
    if raw_error is None:
        assert data["execution_status"] == "COMPLETED"
        assert data["error"] is None
    else:
        assert data["execution_status"] == "FAILED"
        assert data["error"] == {
            "code": "ROUTING_FAILED", "message": "Query routing failed.", "retryable": False,
        }
    assert "private-original-error" not in caplog.text


@pytest.mark.parametrize("raw_reason", ["", "private-dependency-reason"])
async def test_http_sanitizes_halt_reason_and_empty_message_but_preserves_statuses(monkeypatch, raw_reason):
    routing_data = deepcopy(recorded_routing("08_assumption_instead_of_clarify")["routing"])
    routing_data.update(halted=True, halt_reason=raw_reason)
    routing_data["outcomes"][1].update(status="empty", calls=[], referenced_articles=[], message=raw_reason)
    core_result = RouteResult.model_validate(routing_data)
    before = core_result.model_dump(mode="json")
    response = await post(app_with_recorded_router(monkeypatch, "08_assumption_instead_of_clarify", core_result),
                          body())
    assert response.status_code == 200
    data = response.json()
    assert data["execution_status"] == "COMPLETED"
    assert data["error"] is None
    expected = deepcopy(before)
    expected["halt_reason"] = "Routing halted because a dependency returned no results."
    expected["outcomes"][1]["message"] = "Routing details are unavailable."
    assert data["routing"] == expected
    assert core_result.model_dump(mode="json") == before


@pytest.mark.parametrize("result", [None, [], {}, 42, 3.5, True, "citation text", {"nested": [None, {"error": "data"}]}])
@pytest.mark.parametrize("call_group", ["calls", "referenced_articles"])
async def test_http_preserves_arbitrary_tool_result_json(monkeypatch, result, call_group):
    routing_data = deepcopy(recorded_routing("05_offer_check")["routing"])
    routing_data["outcomes"][0][call_group][0]["result"] = deepcopy(result)
    core_result = RouteResult.model_validate(routing_data)
    before = core_result.model_dump(mode="json")
    response = await post(app_with_recorded_router(monkeypatch, "05_offer_check", core_result), body())
    assert response.status_code == 200
    assert response.json()["execution_status"] == "COMPLETED"
    assert response.json()["routing"] == before
    assert core_result.model_dump(mode="json") == before
