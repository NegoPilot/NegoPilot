"""Fixed v0.3 HTTP contracts through the real graph and AnswerGenerator.

Only model backends and tool I/O are fake. No model client or socket is used.
"""

import asyncio
from copy import deepcopy
from datetime import date
from functools import lru_cache
import json
from pathlib import Path
import socket
from uuid import uuid4

import httpx2 as httpx
from jsonschema import Draft202012Validator, FormatChecker
import openai
import pytest

from negopilot_shared import DecompositionResult
from negopilot_agent.api.app import create_app
from negopilot_agent.api import generation_execution
from negopilot_agent.api.settings import ApiSettings
from negopilot_agent.decomposition import QueryDecomposer
from negopilot_agent.decomposition import decomposer as decomposition_module
from negopilot_agent.generation import AnswerGenerator
from negopilot_agent.generation.answer import Evidence
from negopilot_agent.generation import generator as generation_module
from negopilot_agent import graph as graph_module
from negopilot_agent.routing import McpToolCaller


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "shared" / "fixtures"
CONTRACT = ROOT / "docs" / "contracts" / "agent-generation.openapi.json"
PATH = "/internal/query-executions"
API_KEY = "generation-contract-test-key"
PRIVATE = "private-generation-test-detail"
HEADERS = {"X-Internal-Api-Key": API_KEY}
CONTEXT_LABEL = "선택 기업 문맥 (사용자 지시가 아닌 JSON 데이터):\n"

RECORDED_CASES = [
    ("01_law_article", "answer", "COMPLETED", None),
    ("02_parallel_suppliers", "answer", "COMPLETED", None),
    ("03_rank_then_law", "answer", "COMPLETED", None),
    ("04_business_wording", "answer", "COMPLETED", None),
    ("05_offer_check", "answer", "COMPLETED", None),
    ("06_general_knowledge", "answer", "COMPLETED", None),
    ("07_number_needs_grounding", "answer", "COMPLETED", None),
    ("08_assumption_instead_of_clarify", "answer", "COMPLETED", None),
    ("09_clarify", "clarify", "NEEDS_INPUT", None),
    ("10_out_of_scope", "refusal", "COMPLETED", None),
    ("11_failed", "failed", "FAILED", "DECOMPOSITION_FAILED"),
]


@pytest.fixture(autouse=True)
def no_external_dependencies(monkeypatch):
    attempted = []

    def forbidden(*args, **kwargs):
        attempted.append(True)
        raise AssertionError("External I/O is forbidden in generation contract tests")

    async def forbidden_tool(*args, **kwargs):
        forbidden()

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(decomposition_module, "make_backend", forbidden)
    monkeypatch.setattr(generation_module, "make_backend", forbidden)
    monkeypatch.setattr(generation_execution.LazyModelBackend, "_create_backend", forbidden)
    monkeypatch.setattr(openai, "AsyncOpenAI", forbidden)
    monkeypatch.setattr(McpToolCaller, "__call__", forbidden_tool)
    yield
    # A graph may catch a tool exception. It must not hide a network attempt.
    assert not attempted


@lru_cache
def response_validator(status):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert contract["openapi"] == "3.1.0"
    schema = contract["paths"][PATH]["post"]["responses"][str(status)]["content"]["application/json"]["schema"]
    root = {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "components": contract["components"], **schema}
    Draft202012Validator.check_schema(root)
    return Draft202012Validator(root, format_checker=FormatChecker())


def assert_wire(response, status=200):
    assert response.status_code == status
    assert response.headers["content-type"].split(";")[0] == "application/json"
    data = response.json()
    errors = list(response_validator(status).iter_errors(data))
    assert not errors, "\n".join(
        f"{error.json_path}: schema {'/'.join(map(str, error.absolute_schema_path))}"
        for error in errors
    )
    assert API_KEY not in response.text and PRIVATE not in response.text
    return data


def assert_error(response, payload, status, code, retryable=False):
    data = assert_wire(response, status)
    assert data["request_id"] == payload.get("request_id")
    assert data["error"]["code"] == code
    assert data["error"]["retryable"] is retryable
    assert data["error"]["message"].strip()
    return data


def payload(query="생성 답변 확인", **overrides):
    return {"request_id": str(uuid4()), "query": query, "history": [],
            "context": {"selected_supplier_ids": []}} | overrides


async def post(app, body, *, headers=None, **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://generation.test",
    ) as client:
        return await client.post(PATH, json=body, headers=HEADERS if headers is None else headers, **kwargs)


def draft(text, used=()):
    return json.dumps({"answer": text, "used_evidence": list(used)}, ensure_ascii=False)


def cite_all(system, user):
    ids = [line.split('"')[1] for line in user.splitlines()
           if line.startswith('<e id="') and 'citable="yes"' in line]
    if not ids:
        return draft("RFQ는 견적 요청서입니다.")
    return draft("모델 초안의 업무 설명입니다. " + " ".join(f"[{eid}]" for eid in ids), ids)


class FakeBackend:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    async def generate(self, system, user):
        self.requests.append((system, user))
        assert self.responses, "Unexpected model invocation"
        result = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(result, Exception):
            raise result
        return result(system, user) if callable(result) else result


class ReplayCaller:
    def __init__(self, routing, *, fail_references=False):
        self.remaining = deepcopy([
            call for outcome in routing["outcomes"]
            for call in outcome["calls"] + outcome["referenced_articles"]
        ])
        self.fail_references = fail_references
        self.calls = []
        self.unexpected = []

    async def __call__(self, server, tool, args):
        self.calls.append((server, tool, deepcopy(args)))
        for index, record in enumerate(self.remaining):
            if (record["server"], record["tool"], record["args"]) == (server, tool, args):
                self.remaining.pop(index)
                if self.fail_references and tool == "get_article":
                    raise RuntimeError(PRIVATE + API_KEY)
                assert record["error"] is None
                return deepcopy(record["result"])
        self.unexpected.append((server, tool))
        raise AssertionError("Tool call is absent from the shared fixture")


class SupplierCaller:
    def __init__(self, *, fail_supplier=None):
        self.calls = []
        self.fail_supplier = fail_supplier
        self.unexpected = []
        self.result = {"supplier_id": "SUP-TEST", "name": "가상 테스트 기업", "source": "MOCK",
                       "financials": {"sales": 12000, "ratio": 0.45, "note": None},
                       "message": "업무 데이터 문구", "error": "업무 데이터 필드"}

    async def __call__(self, server, tool, args):
        self.calls.append((server, tool, deepcopy(args)))
        if (server, tool) == ("business-mcp", "find_suppliers"):
            return {"matches": [{"supplier_id": "SUP-0041", "name": args["supplier"], "region": "경남"}],
                    "total": 1, "match_type": "exact"}
        if (server, tool) != ("business-mcp", "get_supplier"):
            self.unexpected.append((server, tool))
            raise AssertionError("Unexpected supplier tool")
        if args["supplier"] == self.fail_supplier:
            raise RuntimeError(PRIVATE + API_KEY)
        return deepcopy(self.result) | {"supplier_id": args["supplier"]}


def decomposition(*sub_queries, query="생성 답변 확인"):
    base = {"question": "협력사 정보", "original_text": "협력사 정보", "grounding": "required",
            "confidence": 0.95, "entities": {}, "inputs": [], "depends_on": []}
    return DecompositionResult.model_validate({
        "status": "ok", "original_query": query,
        "sub_queries": [base | sq for sq in sub_queries],
    })


def supplier_part(qid="q1", supplier="SUP-TEST"):
    return {"id": qid, "intent": "supplier_detail", "entities": {"supplier": supplier}}


def load_recorded(name):
    recorded = json.loads((FIXTURES / "routing" / f"{name}.json").read_text(encoding="utf-8"))
    source = json.loads((FIXTURES / recorded["decomposition"]).read_text(encoding="utf-8"))
    return DecompositionResult.model_validate(source["expected"]), recorded["routing"]


class Harness:
    """Observe real core calls without replacing their implementation."""

    def __init__(self, monkeypatch, result, caller, *drafts, mode="stub", timeout=1):
        self.decomposition_backend = FakeBackend(result.model_dump_json(by_alias=True))
        self.answer_backend = FakeBackend(*(drafts or (cite_all,)))
        self.decomposer = QueryDecomposer(backend=self.decomposition_backend)
        self.inputs = []
        self.graph_calls = []
        self.routing_inputs = []
        self.answers = []
        original_decompose = self.decomposer.decompose
        original_generate = AnswerGenerator.generate
        original_build = generation_execution.build_graph
        assert original_build is graph_module.build_graph

        def observe_build(*args, **kwargs):
            self.graph_calls.append((args, kwargs))
            return original_build(*args, **kwargs)

        async def observe_decompose(query, history=None, *, context=None):
            self.inputs.append((query, deepcopy(history), deepcopy(context)))
            return await original_decompose(query, history, context=context)

        async def observe_generate(generator, dec, routing):
            assert type(generator) is AnswerGenerator
            assert generator.backend is self.answer_backend
            self.routing_inputs.append(routing.model_dump(mode="json", by_alias=True))
            answer = await original_generate(generator, dec, routing)
            self.answers.append(answer.model_dump(mode="json"))
            return answer

        monkeypatch.setattr(self.decomposer, "decompose", observe_decompose)
        monkeypatch.setattr(AnswerGenerator, "generate", observe_generate)
        monkeypatch.setattr(generation_execution, "build_graph", observe_build)
        self.caller = caller
        self.app = create_app(
            settings=ApiSettings(mode=mode, internal_api_key=API_KEY, request_timeout_seconds=timeout),
            generation_decomposer=self.decomposer, generation_caller=caller,
            generation_backend=self.answer_backend,
        )

    def assert_completed_graph(self, data, body):
        assert len(self.graph_calls) == 1
        assert self.inputs == [(body["query"], body.get("history", []),
                                body.get("context", {"selected_supplier_ids": []}))]
        assert len(self.routing_inputs) == 1 and len(self.answers) == 1
        assert data["answer"] == self.answers[0]
        assert data["routing"] == self.routing_inputs[0]
        assert data["context"] == body.get("context", {"selected_supplier_ids": []})
        assert data["decomposition"]["original_query"] == body["query"]
        assert not self.caller.unexpected
        answer = data["answer"]
        evidence = {item["id"]: item for item in answer["evidence"]}
        assert len(evidence) == len(answer["evidence"])
        assert len({item["n"] for item in answer["citations"]}) == len(answer["citations"])
        for citation in answer["citations"]:
            item = evidence[citation["evidence_id"]]
            assert item["citable"] is True
            assert citation["label"] == item["label"]
            assert citation["source_url"] == item["source_url"]
        if answer["verification"] is not None:
            assert "ok" not in answer["verification"]


@pytest.mark.parametrize(("name", "kind", "status", "error_code"), RECORDED_CASES)
async def test_shared_fixtures_run_through_graph_and_generator(monkeypatch, name, kind, status, error_code):
    dec, routing = load_recorded(name)
    caller = ReplayCaller(routing)
    harness = Harness(monkeypatch, dec, caller)
    body = payload(dec.original_query)
    data = assert_wire(await post(harness.app, body))
    harness.assert_completed_graph(data, body)
    assert data["schema_version"] == "0.3" and data["mode"] == "stub"
    assert data["request_id"] == body["request_id"]
    assert data["execution_status"] == status
    assert data["generation_status"] == ("FAILED" if kind == "failed" else "COMPLETED")
    assert data["answer"]["kind"] == kind
    assert data["routing"] == routing
    assert data["decomposition"] == dec.model_dump(mode="json", by_alias=True)
    assert caller.remaining == []
    if error_code:
        assert data["error"] == {"code": error_code, "message": "Query decomposition failed.", "retryable": False}
    else:
        assert data["error"] is None
    if kind == "answer":
        assert len(harness.answer_backend.requests) == 1
        assert data["answer"]["grounded"] is (name != "06_general_knowledge")
        assert data["answer"]["verification"] == {
            "attempts": 1, "unknown_citations": [], "ungrounded_numbers": [],
            "ungrounded_articles": [], "masked": False,
        }
    else:
        assert harness.answer_backend.requests == []
        assert data["answer"]["grounded"] is True
        assert data["answer"]["citations"] == []
        assert data["answer"]["verification"] is None
    if kind == "clarify":
        assert data["answer"]["text"] == dec.clarification.question
        assert data["answer"]["options"] == dec.clarification.options
    if name == "07_number_needs_grounding":
        refs = [item for item in data["answer"]["evidence"] if item["kind"] == "article_reference"]
        articles = [call["result"] for call in routing["outcomes"][0]["calls"]
                    if call["tool"] == "get_article"]
        assert refs and len(refs) == len(articles)
        for reference, article in zip(refs, articles, strict=True):
            assert reference["citable"] is True
            assert reference["label"] == article["citation"]
            assert reference["source_url"] == article["source_url"]
        assert {item["id"] for item in refs} <= {
            item["evidence_id"] for item in data["answer"]["citations"]
        }
    if name == "05_offer_check":
        refs = [item for item in data["answer"]["evidence"] if item["kind"] == "referenced_article"]
        assert [item["label"] for item in refs] == [
            call["result"]["citation"] for call in routing["outcomes"][0]["referenced_articles"]
        ]


@pytest.mark.parametrize("mode", ["stub", "live"])
@pytest.mark.parametrize("query,selected,target", [
    ("  이 회사의 재무 정보를 알려줘 😀  ", "SUP-TEST", "SUP-TEST"),
    ("SUP-OTHER의 재무 정보를 알려줘", "SUP-TEST", "SUP-OTHER"),
])
async def test_context_and_twenty_history_entries_reach_real_decomposer(
    monkeypatch, mode, query, selected, target,
):
    dec = decomposition(supplier_part(supplier=target), query=query)
    caller = SupplierCaller()
    harness = Harness(monkeypatch, dec, caller, mode=mode)
    history = [{"role": "user" if index % 2 == 0 else "assistant", "content": f"이력 원문 {index} 😀"}
               for index in range(20)]
    body = payload(query, history=history, context={"selected_supplier_ids": [selected]})
    before = deepcopy(body)
    data = assert_wire(await post(harness.app, body))
    harness.assert_completed_graph(data, body)
    assert body == before and data["mode"] == mode
    prompt = harness.decomposition_backend.requests[0][1]
    encoded = prompt.split(CONTEXT_LABEL, 1)[1].split("\n\n", 1)[0]
    assert json.loads(encoded) == body["context"]
    assert prompt.endswith("분해할 질문:\n" + query)
    for item in history[-6:]:
        assert item["content"] in prompt
    assert caller.calls == [("business-mcp", "get_supplier", {"supplier": target})]
    result = data["routing"]["outcomes"][0]["calls"][0]["result"]
    assert result == caller.result | {"supplier_id": target}
    assert "업무 데이터 필드" in data["answer"]["evidence"][0]["text"]


async def test_omitted_context_and_history_use_contract_defaults(monkeypatch):
    dec, routing = load_recorded("06_general_knowledge")
    harness = Harness(monkeypatch, dec, ReplayCaller(routing))
    body = {"request_id": str(uuid4()), "query": dec.original_query}
    data = assert_wire(await post(harness.app, body))
    harness.assert_completed_graph(data, body)


async def test_model_rewritten_query_is_restored_before_generation(monkeypatch):
    query = "  SUP-TEST의 원문 질문입니다 😀  "
    harness = Harness(monkeypatch, decomposition(supplier_part(), query="모델이 바꾼 질문"), SupplierCaller())
    body = payload(query)
    data = assert_wire(await post(harness.app, body))
    harness.assert_completed_graph(data, body)
    assert data["decomposition"]["original_query"] == query
    assert f"<question>{query}</question>" in harness.answer_backend.requests[0][1]


@pytest.mark.parametrize("case,name", [
    ("today_as_of", "01_law_article"),
    ("disallowed_as_of", "02_parallel_suppliers"),   # offer_check는 check_offer v2부터 as_of를 받는다
    ("disallowed_input", "03_rank_then_law"),
    ("both", "03_rank_then_law"),
])
async def test_latest_decomposition_cleanup_reaches_http_without_retry(monkeypatch, case, name):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2030, 3, 14)

    monkeypatch.setattr(decomposition_module, "date", FixedDate)
    dec, routing = load_recorded(name)
    raw = dec.model_dump(mode="json", by_alias=True)
    target = raw["sub_queries"][1 if name == "03_rank_then_law" else 0]
    if case in {"today_as_of", "both"}:
        target["entities"]["as_of"] = "2030-03-14"
    elif case == "disallowed_as_of":
        target["entities"]["as_of"] = "2025-01-01"
    if case in {"disallowed_input", "both"}:
        target["inputs"] = [{"slot": "supplier", "from": "q1", "select": "top1"}]
        target["depends_on"] = ["q1"]
    caller = ReplayCaller(routing)
    harness = Harness(monkeypatch, dec, caller)
    # Feed unvalidated model JSON so the real QueryDecomposer performs cleanup.
    harness.decomposition_backend.responses = [json.dumps(raw, ensure_ascii=False)]
    body = payload(dec.original_query)
    data = assert_wire(await post(harness.app, body))
    harness.assert_completed_graph(data, body)
    assert data["decomposition"] == dec.model_dump(mode="json", by_alias=True)
    assert data["routing"] == routing and caller.remaining == []
    assert len(harness.decomposition_backend.requests) == 1
    assert "오늘 날짜: 2030-03-14" in harness.decomposition_backend.requests[0][1]


async def test_code_point_and_supplier_id_boundaries_are_accepted(monkeypatch):
    query = "😀" * 4000
    selected = "S" * 100
    harness = Harness(monkeypatch, decomposition(supplier_part(supplier=selected)), SupplierCaller())
    body = payload(query, history=[{"role": "user", "content": "😀" * 4000}],
                   context={"selected_supplier_ids": [selected]})
    data = assert_wire(await post(harness.app, body))
    harness.assert_completed_graph(data, body)
    assert data["execution_status"] == "COMPLETED"


@pytest.mark.parametrize("partial", [False, True])
async def test_routing_error_uses_final_answer_status_and_safe_generator_input(monkeypatch, caplog, partial):
    parts = [supplier_part(supplier="SUP-FAIL")]
    if partial:
        parts.append(supplier_part("q2", "SUP-OK"))
    caller = SupplierCaller(fail_supplier="SUP-FAIL")
    harness = Harness(monkeypatch, decomposition(*parts), caller)
    body = payload()
    data = assert_wire(await post(harness.app, body))
    harness.assert_completed_graph(data, body)
    assert data["execution_status"] == "COMPLETED" and data["generation_status"] == "COMPLETED"
    assert data["error"] is None
    assert data["answer"]["kind"] == ("answer" if partial else "no_result")
    assert data["answer"]["notices"]
    first = data["routing"]["outcomes"][0]
    assert first["status"] == "error"
    assert first["calls"][0]["error"] == "Tool execution failed."
    assert first["message"] == "Routing details are unavailable."
    assert PRIVATE not in caplog.text and API_KEY not in caplog.text
    assert len(harness.answer_backend.requests) == (1 if partial else 0)


@pytest.mark.parametrize("code", ["RFQ_NOT_FOUND", "SUPPLIER_NOT_FOUND", "SUPPLIER_AMBIGUOUS",
                                 "VALUE_OUT_OF_RANGE", "DB_UNAVAILABLE", "INTERNAL"])
async def test_only_public_tool_error_codes_reach_http_response(monkeypatch, caplog, code):
    class FailingCaller(SupplierCaller):
        async def __call__(self, server, tool, args):
            raise RuntimeError(f"[{code}] {PRIVATE} {API_KEY}")

    harness = Harness(monkeypatch, decomposition(supplier_part()), FailingCaller())
    body = payload()
    data = assert_wire(await post(harness.app, body))
    harness.assert_completed_graph(data, body)
    assert data["execution_status"] == "COMPLETED"
    assert data["answer"]["kind"] == "no_result"
    call = data["routing"]["outcomes"][0]["calls"][0]
    assert call["error"] == "Tool execution failed."
    if code in {"DB_UNAVAILABLE", "INTERNAL"}:
        assert "error_code" not in call
        assert code not in json.dumps(data)
    else:
        assert call["error_code"] == code
    assert PRIVATE not in caplog.text and API_KEY not in caplog.text


@pytest.mark.parametrize("partial", [False, True])
async def test_missing_args_is_clarify_only_when_no_answerable_result(monkeypatch, partial):
    parts = [{"id": "q1", "intent": "supplier_detail"}]
    if partial:
        parts.append(supplier_part("q2"))
    harness = Harness(monkeypatch, decomposition(*parts), SupplierCaller())
    body = payload()
    data = assert_wire(await post(harness.app, body))
    harness.assert_completed_graph(data, body)
    assert data["answer"]["kind"] == ("answer" if partial else "clarify")
    assert data["execution_status"] == ("COMPLETED" if partial else "NEEDS_INPUT")
    assert data["generation_status"] == "COMPLETED" and data["error"] is None
    assert data["routing"]["outcomes"][0]["missing"] == ["supplier"]


async def test_unsupported_result_is_completed_no_result_without_model(monkeypatch):
    dec = decomposition({"id": "q1", "intent": "draft_email"})
    harness = Harness(monkeypatch, dec, SupplierCaller())
    body = payload()
    data = assert_wire(await post(harness.app, body))
    harness.assert_completed_graph(data, body)
    assert data["answer"]["kind"] == "no_result"
    assert data["execution_status"] == "COMPLETED" and data["error"] is None
    assert data["routing"]["outcomes"][0]["status"] == "unsupported"
    assert harness.answer_backend.requests == [] and harness.caller.calls == []


@pytest.mark.parametrize("model_output", [None, "not-json"])
async def test_generation_failure_keeps_core_answer_and_evidence(monkeypatch, model_output):
    harness = Harness(monkeypatch, decomposition(supplier_part()), SupplierCaller(), model_output)
    body = payload()
    data = assert_wire(await post(harness.app, body))
    harness.assert_completed_graph(data, body)
    assert data["answer"]["kind"] == "failed" and data["answer"]["evidence"]
    assert data["execution_status"] == "FAILED" and data["generation_status"] == "FAILED"
    assert data["error"] == {
        "code": "GENERATION_FAILED", "message": "Answer generation failed.", "retryable": False,
    }
    assert len(harness.answer_backend.requests) == (1 if model_output is None else 2)


async def test_masked_verification_errors_remain_a_completed_answer(monkeypatch):
    text = "확인된 자료입니다. [E1] 기한 987654일과 제987654조를 적용합니다. [E999]"
    harness = Harness(monkeypatch, decomposition(supplier_part()), SupplierCaller(), draft(text))
    body = payload()
    data = assert_wire(await post(harness.app, body))
    harness.assert_completed_graph(data, body)
    answer = data["answer"]
    assert data["execution_status"] == "COMPLETED" and data["generation_status"] == "COMPLETED"
    assert data["error"] is None and answer["kind"] == "answer" and answer["grounded"] is True
    assert answer["verification"] == {
        "attempts": 2, "unknown_citations": ["E999"], "ungrounded_numbers": ["987654일"],
        "ungrounded_articles": ["제987654조"], "masked": True,
    }
    assert "[확인 필요]" in answer["text"] and "확인된 자료입니다. [1]" in answer["text"]
    assert "987654" not in answer["text"] and "E999" not in answer["text"]
    assert len(harness.answer_backend.requests) == 2


async def test_explicitly_noncitable_evidence_is_rejected_from_http_citations(monkeypatch):
    original_build = generation_module.build_evidence
    restricted = Evidence(
        id="E2", sub_query="q1", source="business", kind="restricted_reference",
        label="인용 금지 참고 자료", text="설명할 때만 참고할 자료입니다.", citable=False,
    )

    def with_restricted_evidence(routing, questions=None):
        evidence = original_build(routing, questions)
        assert [item.id for item in evidence] == ["E1"]
        return [*evidence, restricted.model_copy(deep=True)]

    monkeypatch.setattr(generation_module, "build_evidence", with_restricted_evidence)
    harness = Harness(monkeypatch, decomposition(supplier_part()), SupplierCaller(),
                      draft("확인된 자료입니다. [E1] 참고 자료입니다. [E2]", ["E1", "E2"]))
    body = payload()
    data = assert_wire(await post(harness.app, body))
    harness.assert_completed_graph(data, body)
    answer = data["answer"]
    assert data["execution_status"] == "COMPLETED" and data["generation_status"] == "COMPLETED"
    assert data["error"] is None and answer["kind"] == "answer" and answer["grounded"] is True
    assert answer["evidence"][-1] == restricted.model_dump(mode="json")
    assert answer["verification"] == {
        "attempts": 2, "unknown_citations": ["E2"], "ungrounded_numbers": [],
        "ungrounded_articles": [], "masked": True,
    }
    assert [item["evidence_id"] for item in answer["citations"]] == ["E1"]
    assert "확인된 자료입니다. [1]" in answer["text"] and "[E2]" not in answer["text"]
    assert len(harness.answer_backend.requests) == 2
    assert any(line.startswith('<e id="E2"') and 'citable="no"' in line
               for line in harness.answer_backend.requests[0][1].splitlines())


async def test_citation_retry_preserves_corrected_model_text_and_verification(monkeypatch):
    harness = Harness(monkeypatch, decomposition(supplier_part()), SupplierCaller(),
                      draft("첫 초안입니다. [E999]"), draft("고친 모델 본문입니다. [E1]"))
    body = payload()
    data = assert_wire(await post(harness.app, body))
    harness.assert_completed_graph(data, body)
    assert data["answer"]["text"] == "고친 모델 본문입니다. [1]"
    assert data["answer"]["verification"] == {
        "attempts": 2, "unknown_citations": [], "ungrounded_numbers": [],
        "ungrounded_articles": [], "masked": False,
    }
    assert len(harness.answer_backend.requests) == 2
    assert "E999" in harness.answer_backend.requests[1][1]


async def test_long_answer_with_blank_prefix_is_preserved_for_conversation_storage(monkeypatch):
    text = " " * 4000 + "저장할 모델 본문입니다. [E1]"
    harness = Harness(monkeypatch, decomposition(supplier_part()), SupplierCaller(), draft(text))
    body = payload()
    data = assert_wire(await post(harness.app, body))
    harness.assert_completed_graph(data, body)
    assert data["answer"]["text"] == " " * 4000 + "저장할 모델 본문입니다. [1]"
    assert len(data["answer"]["text"]) > 4000


async def test_failed_reference_lookup_preserves_primary_evidence(monkeypatch, caplog):
    dec, routing = load_recorded("05_offer_check")
    caller = ReplayCaller(routing, fail_references=True)
    harness = Harness(monkeypatch, dec, caller)
    body = payload(dec.original_query)
    data = assert_wire(await post(harness.app, body))
    harness.assert_completed_graph(data, body)
    assert data["execution_status"] == "COMPLETED" and data["error"] is None
    outcome = data["routing"]["outcomes"][0]
    assert outcome["status"] == "ok" and outcome["calls"] == routing["outcomes"][0]["calls"]
    assert len(outcome["referenced_articles"]) == 3 and caller.remaining == []
    assert all(item["error"] == "Tool execution failed." for item in outcome["referenced_articles"])
    assert all(item["kind"] != "referenced_article" for item in data["answer"]["evidence"])
    assert PRIVATE not in caplog.text and API_KEY not in caplog.text


@pytest.mark.parametrize("override", [
    {"query": " "}, {"query": "😀" * 4001}, {"query": None}, {"extra": PRIVATE},
    {"history": None}, {"history": [{"role": "user", "content": "a"}] * 21},
    {"history": [{"role": "system", "content": "a"}]},
    {"history": [{"role": "user", "content": " "}]},
    {"history": [{"role": "user", "content": "a", "extra": PRIVATE}]},
    {"context": None}, {"context": {"selected_supplier_ids": None}},
    {"context": {"selected_supplier_ids": ["SUP-A", "SUP-B"]}},
    {"context": {"selected_supplier_ids": ["has space"]}},
    {"context": {"selected_supplier_ids": ["x" * 101]}},
    {"context": {"selected_supplier_ids": [], "name": PRIVATE}},
])
async def test_invalid_request_is_rejected_before_graph_or_model(monkeypatch, override):
    harness = Harness(monkeypatch, decomposition(supplier_part()), SupplierCaller())
    body = payload() | override
    assert_error(await post(harness.app, body), body, 400, "VALIDATION_ERROR")
    assert harness.inputs == [] and harness.answer_backend.requests == []
    assert harness.graph_calls == []
    assert harness.caller.calls == []


@pytest.mark.parametrize("headers", [{}, {"X-Internal-Api-Key": "wrong-key"}])
async def test_authentication_is_checked_before_graph(monkeypatch, headers):
    harness = Harness(monkeypatch, decomposition(supplier_part()), SupplierCaller())
    body = payload()
    assert_error(await post(harness.app, body, headers=headers), body, 401, "UNAUTHORIZED")
    assert harness.inputs == [] and harness.answer_backend.requests == []
    assert harness.graph_calls == []
    assert harness.caller.calls == []


@pytest.mark.parametrize("request_id", [None, "not-a-uuid", 123, "missing"])
async def test_internal_request_id_is_a_required_uuid(monkeypatch, request_id):
    harness = Harness(monkeypatch, decomposition(supplier_part()), SupplierCaller())
    body = payload(request_id=request_id)
    if request_id == "missing":
        del body["request_id"]
    assert_error(await post(harness.app, body), {"request_id": None}, 400, "VALIDATION_ERROR")
    assert harness.graph_calls == []


@pytest.mark.parametrize("stage", ["decomposition", "generation"])
@pytest.mark.parametrize("failure,status,code,retryable", [
    (httpx.ConnectError(PRIVATE + API_KEY), 503, "UPSTREAM_UNAVAILABLE", True),
    (TimeoutError(PRIVATE + API_KEY), 504, "UPSTREAM_TIMEOUT", True),
    (RuntimeError(PRIVATE + API_KEY), 500, "INTERNAL_ERROR", False),
])
async def test_model_errors_are_safe_http_errors_without_stub_fallback(
    monkeypatch, caplog, stage, failure, status, code, retryable,
):
    harness = Harness(monkeypatch, decomposition(supplier_part()), SupplierCaller(), mode="live")
    backend = harness.decomposition_backend if stage == "decomposition" else harness.answer_backend
    backend.responses = [failure]
    body = payload()
    assert_error(await post(harness.app, body), body, status, code, retryable)
    assert harness.answers == [] and len(backend.requests) == 1
    assert PRIVATE not in caplog.text and API_KEY not in caplog.text


@pytest.mark.parametrize("stage", ["decomposition", "generation"])
@pytest.mark.parametrize("kind,status,code", [
    ("connection", 503, "UPSTREAM_UNAVAILABLE"),
    ("rate_limit", 503, "UPSTREAM_UNAVAILABLE"),
    ("timeout", 504, "UPSTREAM_TIMEOUT"),
])
async def test_openai_sdk_errors_follow_the_fixed_http_contract(monkeypatch, caplog, stage, kind, status, code):
    request = httpx.Request("POST", f"https://model.test/{PRIVATE}", headers={"Authorization": API_KEY})
    if kind == "connection":
        failure = openai.APIConnectionError(message=PRIVATE + API_KEY, request=request)
    elif kind == "timeout":
        failure = openai.APITimeoutError(request=request)
    else:
        failure = openai.RateLimitError(
            PRIVATE + API_KEY, response=httpx.Response(429, request=request), body={"message": PRIVATE},
        )
    harness = Harness(monkeypatch, decomposition(supplier_part()), SupplierCaller(), mode="live")
    backend = harness.decomposition_backend if stage == "decomposition" else harness.answer_backend
    backend.responses = [failure]
    body = payload()
    assert_error(await post(harness.app, body), body, status, code, True)
    assert harness.answers == [] and len(backend.requests) == 1
    assert PRIVATE not in caplog.text and API_KEY not in caplog.text


async def test_request_timeout_cancels_generation_inside_the_real_graph(monkeypatch):
    harness = Harness(monkeypatch, decomposition(supplier_part()), SupplierCaller(), timeout=0.2)
    started = []
    cancelled = []

    async def blocked_model(system, user):
        started.append(True)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(harness.answer_backend, "generate", blocked_model)
    body = payload()
    assert_error(await post(harness.app, body), body, 504, "UPSTREAM_TIMEOUT", True)
    assert started == [True] and cancelled == [True]
    assert len(harness.graph_calls) == 1 and harness.answers == []
