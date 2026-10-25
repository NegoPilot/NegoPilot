"""Direct-chat scenarios through ASGI and the real Agent graph.

The built-in stub backends supply only model/tool I/O. The Router and
AnswerGenerator run normally and every response uses the fixed v0.3 contract.
"""

from copy import deepcopy
import json

import pytest

from negopilot_agent import graph as graph_module
from negopilot_agent.api import generation_execution, generation_stubs
from negopilot_agent.api.app import create_app
from negopilot_agent.api.settings import ApiSettings
from negopilot_agent.decomposition import QueryDecomposer
from negopilot_agent.generation import AnswerGenerator

from tests.test_generation_http_contract import (
    API_KEY, CONTEXT_LABEL, FIXTURES, PRIVATE, ROOT, assert_wire,
    no_external_dependencies, payload, post,
)


pytestmark = pytest.mark.usefixtures("no_external_dependencies")

FINANCIAL_QUERY = "이 회사의 재무 상태를 알려줘"
AMBIGUOUS_QUERY = "해솔에 대해 알려줘"
PAYMENT_QUERY = "결제 90일로 해도 돼?"


def suppliers():
    directory = ROOT / "docs" / "contracts" / "fixtures"
    base = json.loads((directory / "supplier-chat.json").read_text(encoding="utf-8"))["suppliers"]
    extra = json.loads((directory / "direct-chat.json").read_text(encoding="utf-8"))["suppliers"]
    return {item["supplier_id"]: item for item in [*base, *extra]}


def followup_history(query, response):
    return [{"role": "user", "content": query},
            {"role": "assistant", "content": response["answer"]["text"]}]


def only_outcome(data):
    assert len(data["routing"]["outcomes"]) == 1
    return data["routing"]["outcomes"][0]


def assert_answer(data):
    assert data["execution_status"] == "COMPLETED"
    assert data["answer"]["kind"] == "answer"
    assert data["answer"]["options"] == []
    assert data["answer"]["grounded"] is True
    assert data["answer"]["citations"]
    assert data["answer"]["verification"] == {
        "attempts": 1, "unknown_citations": [], "ungrounded_numbers": [],
        "ungrounded_articles": [], "masked": False,
    }


def assert_clarify(data):
    assert data["execution_status"] == "NEEDS_INPUT"
    assert data["answer"]["kind"] == "clarify"
    assert data["answer"]["text"].strip()
    assert data["answer"]["citations"] == []
    assert data["answer"]["verification"] is None


def assert_supplier_answer(data, supplier_id):
    assert_answer(data)
    outcome = only_outcome(data)
    assert outcome["status"] == "ok"
    details = [call for call in outcome["calls"] if call["tool"] == "get_supplier"]
    assert len(details) == 1
    assert details[0]["args"] == {"supplier": supplier_id}
    assert details[0]["result"] == suppliers()[supplier_id] | {"source": "STUB", "stub": True}
    assert any(item["kind"] == "supplier" and item["meta"]["supplier_id"] == supplier_id
               for item in data["answer"]["evidence"])


class DirectChatHarness:
    """Observe core execution while retaining the built-in fake I/O backends."""

    def __init__(self, monkeypatch, *, failing_tool=None):
        self.inputs = []
        self.graph_calls = []
        self.decomposition_prompts = []
        self.generation_prompts = []
        self.calls = []
        self.routing_inputs = []
        self.answers = []
        self.last_calls = []
        self.last_generation_count = 0
        original_build = generation_execution.build_graph
        original_decompose = QueryDecomposer.decompose
        original_generate = AnswerGenerator.generate
        original_decomposition_model = generation_stubs.FixtureDecompositionBackend.generate
        original_generation_model = generation_stubs.FixtureGenerationBackend.generate
        original_tool = generation_stubs.GenerationStubToolCaller.__call__
        assert original_build is graph_module.build_graph

        def observe_build(*args, **kwargs):
            self.graph_calls.append(True)
            return original_build(*args, **kwargs)

        async def observe_decompose(decomposer, query, history=None, *, context=None):
            assert type(decomposer) is QueryDecomposer
            assert isinstance(decomposer.backend, generation_stubs.FixtureDecompositionBackend)
            self.inputs.append((query, deepcopy(history), deepcopy(context)))
            return await original_decompose(decomposer, query, history, context=context)

        async def observe_generate(generator, decomposition, routing):
            assert type(generator) is AnswerGenerator
            assert isinstance(generator.backend, generation_stubs.FixtureGenerationBackend)
            self.routing_inputs.append(routing.model_dump(mode="json", by_alias=True))
            answer = await original_generate(generator, decomposition, routing)
            self.answers.append(answer.model_dump(mode="json"))
            return answer

        async def observe_decomposition_model(backend, system, user):
            self.decomposition_prompts.append(user)
            return await original_decomposition_model(backend, system, user)

        async def observe_generation_model(backend, system, user):
            self.generation_prompts.append(user)
            return await original_generation_model(backend, system, user)

        async def observe_tool(caller, server, tool, args):
            record = {"server": server, "tool": tool, "args": deepcopy(args)}
            self.calls.append(record)
            if tool == failing_tool:
                raise RuntimeError(PRIVATE + API_KEY)
            result = await original_tool(caller, server, tool, args)
            record["result"] = deepcopy(result)
            return result

        monkeypatch.setattr(generation_execution, "build_graph", observe_build)
        monkeypatch.setattr(QueryDecomposer, "decompose", observe_decompose)
        monkeypatch.setattr(AnswerGenerator, "generate", observe_generate)
        monkeypatch.setattr(generation_stubs.FixtureDecompositionBackend, "generate", observe_decomposition_model)
        monkeypatch.setattr(generation_stubs.FixtureGenerationBackend, "generate", observe_generation_model)
        monkeypatch.setattr(generation_stubs.GenerationStubToolCaller, "__call__", observe_tool)
        self.app = create_app(settings=ApiSettings(
            mode="stub", internal_api_key=API_KEY, request_timeout_seconds=2,
        ))

    async def turn(self, query, *, history=None, selected=None):
        body = payload(query, history=deepcopy(history or []),
                       context={"selected_supplier_ids": list(selected or [])})
        previous = (len(self.graph_calls), len(self.inputs), len(self.answers), len(self.routing_inputs))
        call_start = len(self.calls)
        generation_start = len(self.generation_prompts)
        data = assert_wire(await post(self.app, body))
        assert (len(self.graph_calls), len(self.inputs), len(self.answers), len(self.routing_inputs)) == tuple(
            count + 1 for count in previous
        )
        self.last_calls = deepcopy(self.calls[call_start:])
        self.last_generation_count = len(self.generation_prompts) - generation_start
        assert self.inputs[-1] == (query, body["history"], body["context"])
        assert data["request_id"] == body["request_id"]
        assert data["schema_version"] == "0.3" and data["mode"] == "stub"
        assert data["generation_status"] == "COMPLETED" and data["error"] is None
        assert data["context"] == body["context"]
        assert data["decomposition"]["original_query"] == query
        assert data["answer"] == self.answers[-1]
        assert data["routing"] == self.routing_inputs[-1]
        encoded = self.decomposition_prompts[-1].split(CONTEXT_LABEL, 1)[1].split("\n\n", 1)[0]
        assert json.loads(encoded) == body["context"]
        for item in body["history"][-6:]:
            assert item["content"] in self.decomposition_prompts[-1]
        evidence = {item["id"]: item for item in data["answer"]["evidence"]}
        assert len(evidence) == len(data["answer"]["evidence"])
        citations = data["answer"]["citations"]
        assert len({item["n"] for item in citations}) == len(citations)
        for citation in citations:
            source = evidence[citation["evidence_id"]]
            assert source["citable"] is True
            assert citation["label"] == source["label"]
            assert citation["source_url"] == source["source_url"]
        return data


@pytest.mark.parametrize("query,args,expected_ids", [
    ("협력사 목록 보여줘", {}, {"SUP-0034", "SUP-0041", "SUP-0052", "SUP-0060"}),
    ("부산 협력사 찾아줘", {"region": "부산"}, {"SUP-0041"}),
    ("경남 협력사 찾아줘", {"region": "경남"}, {"SUP-0060"}),
    ("프레스 협력사 찾아줘", {"process": "프레스"}, {"SUP-0052", "SUP-0060"}),
])
async def test_supplier_search_does_not_require_selection_or_process(monkeypatch, query, args, expected_ids):
    harness = DirectChatHarness(monkeypatch)
    data = await harness.turn(query)
    assert_answer(data)
    assert data["decomposition"]["sub_queries"][0]["intent"] == "supplier_search"
    assert len(harness.last_calls) == 1
    call = harness.last_calls[0]
    assert (call["server"], call["tool"], call["args"]) == ("business-mcp", "search_suppliers", args)
    assert {item["supplier_id"] for item in call["result"]} == expected_ids
    assert only_outcome(data)["calls"][0]["result"] == call["result"]
    for row in call["result"]:
        source = suppliers()[row["supplier_id"]]
        assert row["name"] == source["name"] and row["region"] == source["region"]
        assert row["source"] == "STUB" and row["stub"] is True
        assert {key: row[key] for key in ("process", "material", "monthly_capacity")} in source["capabilities"]


@pytest.mark.parametrize("query", ["하도급법상 대금 지급 기한은 며칠이야?", PAYMENT_QUERY])
async def test_payment_questions_without_selected_company_use_grounded_law_check(monkeypatch, query):
    harness = DirectChatHarness(monkeypatch)
    data = await harness.turn(query)
    assert_answer(data)
    assert data["decomposition"]["sub_queries"][0]["intent"] == "law_check"
    source = json.loads((FIXTURES / "routing" / "07_number_needs_grounding.json").read_text(encoding="utf-8"))
    recorded = source["routing"]["outcomes"][0]["calls"]
    # 라우터는 분해 질문 뒤에 사용자 원문을 붙여 검색한다. 원문은 질문마다 달라서 분해 질문 부분만 비교한다
    question = recorded[0]["args"]["query"].removesuffix(" 하도급법상 대금 지급 기한은 며칠이야?")

    def call_key(item):
        args = dict(item["args"])
        query = args.pop("query", None)
        return item["server"], item["tool"], args, query is None or query.startswith(question)

    assert [call_key(item) for item in harness.last_calls] == [call_key(item) for item in recorded]
    for actual, expected in zip(only_outcome(data)["calls"], recorded, strict=True):
        expected_result = ([item | {"stub": True} for item in expected["result"]]
                           if expected["tool"] == "search_regulation" else expected["result"] | {"stub": True})
        assert actual["result"] == expected_result
    evidence = data["answer"]["evidence"]
    hits = [item for item in evidence if item["kind"] == "regulation_hit"]
    references = [item for item in evidence if item["kind"] == "article_reference"]
    assert hits and references
    assert [item["label"] for item in hits] == [item["citation"] for item in recorded[0]["result"]]
    articles = [item["result"] for item in recorded if item["tool"] == "get_article"]
    assert len(references) == len(articles)
    for reference, article in zip(references, articles, strict=True):
        assert reference["citable"] is True
        assert reference["label"] == article["citation"]
        assert reference["source_url"] == article["source_url"]
    assert {item["evidence_id"] for item in data["answer"]["citations"]} <= {
        item["id"] for item in hits + references
    }


@pytest.mark.parametrize("selected", [[], ["SUP-0034"]])
async def test_unique_company_name_resolves_then_reads_that_company(monkeypatch, selected):
    harness = DirectChatHarness(monkeypatch)
    data = await harness.turn("해솔정밀에 대해 알려줘", selected=selected)
    assert_supplier_answer(data, "SUP-0041")
    assert [(item["tool"], item["args"]) for item in harness.last_calls] == [
        ("find_suppliers", {"supplier": "해솔정밀", "limit": 10}),
        ("get_supplier", {"supplier": "SUP-0041"}),
    ]
    assert harness.last_calls[0]["result"] == {
        "matches": [{key: suppliers()["SUP-0041"][key] for key in ("supplier_id", "name", "region")}],
        "total": 1, "match_type": "exact",
    }


@pytest.mark.parametrize("reply", ["SUP-0041", "해솔정밀 (SUP-0041)"])
async def test_missing_target_clarifies_then_short_reply_resumes_original_question(monkeypatch, reply):
    harness = DirectChatHarness(monkeypatch)
    first = await harness.turn(FINANCIAL_QUERY)
    assert_clarify(first)
    assert first["decomposition"]["status"] == "clarify"
    assert first["routing"]["status"] == "not_routed"
    assert first["answer"]["options"]
    assert harness.last_calls == [] and harness.last_generation_count == 0
    second = await harness.turn(reply, history=followup_history(FINANCIAL_QUERY, first))
    assert_supplier_answer(second, "SUP-0041")
    query = second["decomposition"]["sub_queries"][0]
    assert query["entities"]["supplier"] == "SUP-0041"
    assert FINANCIAL_QUERY in query["question"]
    assert [(item["tool"], item["args"]) for item in harness.last_calls] == [
        ("get_supplier", {"supplier": "SUP-0041"}),
    ]
    assert f"<question>{reply}</question>" in harness.generation_prompts[-1]
    assert FINANCIAL_QUERY in harness.generation_prompts[-1]


@pytest.mark.parametrize("reply,supplier_id", [
    ("SUP-0041", "SUP-0041"), ("해솔금형 (SUP-0060)", "SUP-0060"),
])
async def test_ambiguous_name_returns_candidates_then_resumes_selection(monkeypatch, reply, supplier_id):
    harness = DirectChatHarness(monkeypatch)
    first = await harness.turn(AMBIGUOUS_QUERY)
    assert_clarify(first)
    assert first["decomposition"]["status"] == "ok"
    assert len(harness.last_calls) == 1 and harness.last_calls[0]["tool"] == "find_suppliers"
    assert harness.last_calls[0]["args"] == {"supplier": "해솔", "limit": 10}
    assert harness.last_calls[0]["result"] == {
        "matches": [{key: suppliers()[sid][key] for key in ("supplier_id", "name", "region")}
                    for sid in ("SUP-0041", "SUP-0060")],
        "total": 2, "match_type": "partial",
    }
    options = first["answer"]["options"]
    assert len(options) == 2
    for sid in ("SUP-0041", "SUP-0060"):
        assert any(sid in option and suppliers()[sid]["name"] in option for option in options)
    assert only_outcome(first)["status"] == "missing_args"
    assert only_outcome(first)["missing"] == ["supplier"]
    assert harness.last_generation_count == 0
    second = await harness.turn(reply, history=followup_history(AMBIGUOUS_QUERY, first))
    assert_supplier_answer(second, supplier_id)
    assert AMBIGUOUS_QUERY in second["decomposition"]["sub_queries"][0]["question"]
    assert [(item["tool"], item["args"]) for item in harness.last_calls] == [
        ("get_supplier", {"supplier": supplier_id}),
    ]


@pytest.mark.parametrize("query,tool", [
    ("없는기업에 대해 알려줘", "find_suppliers"), ("제주 협력사 찾아줘", "search_suppliers"),
])
async def test_no_match_is_empty_no_result_without_clarification_options(monkeypatch, query, tool):
    # 검토 루프를 끈 한 번 실행의 계약. 루프가 켜지면 결과가 있는 인접 지역 검색을 options로 권한다 (test_review_loop.py)
    monkeypatch.setenv("NEGOPILOT_REVIEW_ROUNDS", "0")
    harness = DirectChatHarness(monkeypatch)
    data = await harness.turn(query)
    assert data["execution_status"] == "COMPLETED"
    assert data["answer"]["kind"] == "no_result"
    assert data["answer"]["options"] == [] and data["answer"]["citations"] == []
    assert data["answer"]["evidence"] == [] and data["answer"]["verification"] is None
    assert only_outcome(data)["status"] == "empty"
    assert len(harness.last_calls) == 1 and harness.last_calls[0]["tool"] == tool
    result = harness.last_calls[0]["result"]
    assert result == ([] if tool == "search_suppliers" else {"matches": [], "total": 0, "match_type": "partial"})
    assert harness.last_generation_count == 0


@pytest.mark.parametrize("query,tool", [
    ("협력사 목록 보여줘", "search_suppliers"),
    ("해솔정밀에 대해 알려줘", "find_suppliers"),
    ("해솔정밀에 대해 알려줘", "get_supplier"),
    (PAYMENT_QUERY, "search_regulation"),
])
async def test_tool_failure_is_error_no_result_without_candidate_options(monkeypatch, caplog, query, tool):
    harness = DirectChatHarness(monkeypatch, failing_tool=tool)
    data = await harness.turn(query)
    assert data["execution_status"] == "COMPLETED"
    assert data["answer"]["kind"] == "no_result"
    assert data["answer"]["options"] == [] and data["answer"]["citations"] == []
    assert data["answer"]["notices"]
    outcome = only_outcome(data)
    assert outcome["status"] == "error"
    failed = [item for item in outcome["calls"] if item["error"] is not None]
    assert len(failed) == 1 and failed[0]["tool"] == tool
    assert failed[0]["error"] == "Tool execution failed."
    assert outcome["message"] == "Routing details are unavailable."
    assert harness.last_generation_count == 0
    assert PRIVATE not in caplog.text and API_KEY not in caplog.text


@pytest.mark.parametrize("pending", [FINANCIAL_QUERY, AMBIGUOUS_QUERY])
@pytest.mark.parametrize("query,intent,first_tool", [
    (PAYMENT_QUERY, "law_check", "search_regulation"),
    ("경남 협력사 찾아줘", "supplier_search", "search_suppliers"),
    ("해솔정밀에 대해 알려줘", "supplier_detail", "find_suppliers"),
])
async def test_new_topic_is_not_forced_into_pending_clarification(
    monkeypatch, pending, query, intent, first_tool,
):
    harness = DirectChatHarness(monkeypatch)
    first = await harness.turn(pending)
    assert_clarify(first)
    second = await harness.turn(query, history=followup_history(pending, first))
    assert_answer(second)
    assert second["decomposition"]["sub_queries"][0]["intent"] == intent
    assert pending not in second["decomposition"]["sub_queries"][0]["question"]
    assert harness.last_calls[0]["tool"] == first_tool
    if intent == "supplier_detail":
        assert_supplier_answer(second, "SUP-0041")
    elif intent == "supplier_search":
        assert {item["supplier_id"] for item in harness.last_calls[0]["result"]} == {"SUP-0060"}
