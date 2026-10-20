"""쿼리 분해 단위 테스트.

실제 API 대신 가짜 HTTP 전송을 Anthropic SDK에 끼워서, SDK의 요청 생성과 구조화 출력 파싱까지 그대로 거친다.
실제 모델로 평가하려면 scripts/eval_decomposition.py 를 쓴다.

    uv run python -m pytest -q
"""

import json
from pathlib import Path

import anthropic
import httpx2
import pytest
from langgraph.graph import END, START, StateGraph

from negopilot_shared import DecompositionResult, load_intents

from negopilot_agent.decomposition import (
    DecomposerSettings,
    DecompositionState,
    QueryDecomposer,
    make_decompose_node,
)
from negopilot_agent.decomposition.backends import OllamaBackend
from negopilot_agent.decomposition.prompt import EXAMPLES, build_system_prompt

FIXTURES = sorted((Path(__file__).resolve().parents[2] / "shared" / "fixtures" / "decomposition").glob("*.json"))


def message_json(text: str, stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


class FakeClaude:
    """준비된 응답을 차례로 돌려주고, 받은 요청을 기록한다."""

    def __init__(self, *responses: dict):
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(200, json=self.responses.pop(0))

    def body(self, i: int = 0) -> dict:
        return json.loads(self.requests[i].content)

    def decomposer(self, **settings) -> QueryDecomposer:
        client = anthropic.AsyncAnthropic(
            api_key="test-key", max_retries=0,
            http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(self.handler)))
        return QueryDecomposer(client=client, settings=DecomposerSettings(**{"backend": "anthropic", **settings}))


def fixture(name: str) -> dict:
    return json.loads(next(p for p in FIXTURES if p.stem.startswith(name)).read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
async def test_parses_every_contract_example(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    fake = FakeClaude(message_json(json.dumps(data["expected"], ensure_ascii=False)))
    result = await fake.decomposer().decompose(data["query"])
    assert result == DecompositionResult.model_validate(data["expected"])


async def test_request_shape():
    data = fixture("01")
    fake = FakeClaude(message_json(json.dumps(data["expected"], ensure_ascii=False)))
    await fake.decomposer(model="claude-opus-5-5", effort="low").decompose(data["query"])
    body = fake.body()

    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    # 시스템 프롬프트는 캐시하고, 요청마다 바뀌는 날짜는 사용자 메시지에 둔다
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "오늘 날짜" not in body["system"][0]["text"]
    assert "오늘 날짜" in body["messages"][0]["content"]
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in fake.requests[0].headers["anthropic-beta"]


async def test_fallbacks_can_be_turned_off():
    data = fixture("01")
    fake = FakeClaude(message_json(json.dumps(data["expected"], ensure_ascii=False)))
    await fake.decomposer(use_fallbacks=False).decompose(data["query"])
    assert "fallbacks" not in fake.body()
    assert "anthropic-beta" not in fake.requests[0].headers


async def test_retries_once_with_validation_feedback():
    data = fixture("01")
    bad = json.loads(json.dumps(data["expected"]))
    bad["sub_queries"][0]["intent"] = "weather"
    fake = FakeClaude(message_json(json.dumps(bad, ensure_ascii=False)),
                      message_json(json.dumps(data["expected"], ensure_ascii=False)))

    result = await fake.decomposer().decompose(data["query"])

    assert result.status == "ok"
    assert len(fake.requests) == 2
    retry_prompt = fake.body(1)["messages"][0]["content"]
    assert "계약 형식을 어겼다" in retry_prompt and "등록표에 없다" in retry_prompt


async def test_fails_after_second_invalid_output():
    data = fixture("01")
    bad = json.loads(json.dumps(data["expected"]))
    bad["sub_queries"][0]["grounding"] = "none"
    fake = FakeClaude(message_json(json.dumps(bad)), message_json(json.dumps(bad)))

    result = await fake.decomposer().decompose(data["query"])

    assert result.status == "failed" and result.sub_queries == []
    assert len(fake.requests) == 2


async def test_refusal_returns_failed_without_retry():
    fake = FakeClaude(message_json("", stop_reason="refusal"))
    result = await fake.decomposer().decompose("아무 질문")
    assert result.status == "failed"
    assert len(fake.requests) == 1


async def test_original_query_is_kept_verbatim():
    data = fixture("06")
    expected = json.loads(json.dumps(data["expected"]))
    expected["original_query"] = "RFQ가 무엇인가요?"   # 모델이 질문을 다듬은 경우
    fake = FakeClaude(message_json(json.dumps(expected, ensure_ascii=False)))
    result = await fake.decomposer().decompose(data["query"])
    assert result.original_query == data["query"]


async def test_history_goes_to_user_message():
    data = fixture("01")
    fake = FakeClaude(message_json(json.dumps(data["expected"], ensure_ascii=False)))
    history = [{"role": "user", "content": "SUP-0003 어때?"}, {"role": "assistant", "content": "품질 A등급입니다."}]
    await fake.decomposer().decompose(data["query"], history)
    content = fake.body()["messages"][0]["content"]
    assert "사용자: SUP-0003 어때?" in content and "어시스턴트: 품질 A등급입니다." in content


async def test_api_errors_propagate_for_harness_recovery():
    class Boom(FakeClaude):
        def handler(self, request):
            self.requests.append(request)
            return httpx2.Response(503, json={"type": "error", "error": {"type": "overloaded_error",
                                                                        "message": "overloaded"}})
    with pytest.raises(anthropic.APIStatusError):
        await Boom().decomposer().decompose("하도급법 제4조 보여줘")


def test_prompt_examples_follow_contract():
    for ex in EXAMPLES:
        result = DecompositionResult.model_validate(ex["result"])
        assert result.original_query == ex["query"]


def test_prompt_examples_do_not_leak_eval_fixtures():
    fixture_queries = {json.loads(p.read_text(encoding="utf-8"))["query"] for p in FIXTURES}
    assert not fixture_queries & {ex["query"] for ex in EXAMPLES}


def test_system_prompt_lists_every_intent():
    prompt = build_system_prompt()
    for intent in load_intents():
        assert f"- {intent} [" in prompt


async def test_langgraph_node():
    data = fixture("03")
    fake = FakeClaude(message_json(json.dumps(data["expected"], ensure_ascii=False)))
    graph = StateGraph(DecompositionState)
    graph.add_node("decompose_query", make_decompose_node(fake.decomposer()))
    graph.add_edge(START, "decompose_query")
    graph.add_edge("decompose_query", END)

    state = await graph.compile().ainvoke({"query": data["query"]})

    assert isinstance(state["decomposition"], DecompositionResult)
    assert state["decomposition"].parallel_groups() == [["q1", "q2"]]


class FakeOllama:
    """Ollama /api/chat 응답을 흉내 낸다."""

    def __init__(self, *contents: str, done_reason: str = "stop"):
        self.contents = list(contents)
        self.done_reason = done_reason
        self.requests: list[httpx2.Request] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(200, json={"model": "llama3.1:8b", "done": True, "done_reason": self.done_reason,
                                          "message": {"role": "assistant", "content": self.contents.pop(0)}})

    def decomposer(self) -> QueryDecomposer:
        http = httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler))
        backend = OllamaBackend("llama3.1:8b", http_client=http)
        return QueryDecomposer(settings=DecomposerSettings(backend="ollama", model="llama3.1:8b"), backend=backend)


async def test_ollama_backend_request_and_parse():
    data = fixture("08")
    fake = FakeOllama(json.dumps(data["expected"], ensure_ascii=False))
    result = await fake.decomposer().decompose(data["query"])

    assert result == DecompositionResult.model_validate(data["expected"])
    body = json.loads(fake.requests[0].content)
    assert fake.requests[0].url.path == "/api/chat"
    assert body["model"] == "llama3.1:8b" and body["stream"] is False
    assert body["messages"][0]["role"] == "system" and body["messages"][1]["role"] == "user"
    assert body["options"]["temperature"] == 0
    # 로컬 모델용 스키마에는 $ref 가 남아 있지 않아야 한다
    assert "$ref" not in json.dumps(body["format"]) and "$defs" not in body["format"]


async def test_ollama_retry_and_truncation():
    data = fixture("01")
    fake = FakeOllama("{\"status\": \"ok\"}", json.dumps(data["expected"], ensure_ascii=False))
    assert (await fake.decomposer().decompose(data["query"])).status == "ok"
    assert len(fake.requests) == 2

    cut = FakeOllama("{\"schema_version\": ", done_reason="length")
    assert (await cut.decomposer().decompose(data["query"])).status == "failed"


def test_unknown_backend():
    with pytest.raises(ValueError, match="알 수 없는 백엔드"):
        QueryDecomposer(settings=DecomposerSettings(backend="gpt"))


async def test_dependency_without_inputs_is_dropped():
    data = fixture("03")
    with_dep = json.loads(json.dumps(data["expected"]))
    with_dep["sub_queries"][1]["depends_on"] = ["q1"]     # 값을 받지 않는데 의존만 건 경우
    fake = FakeClaude(message_json(json.dumps(with_dep, ensure_ascii=False)))
    result = await fake.decomposer().decompose(data["query"])
    assert result.sub_queries[1].depends_on == []
    assert result.parallel_groups() == [["q1", "q2"]]


async def test_dependency_with_inputs_is_kept():
    data = fixture("08")
    fake = FakeClaude(message_json(json.dumps(data["expected"], ensure_ascii=False)))
    result = await fake.decomposer().decompose(data["query"])
    assert result.sub_queries[1].depends_on == ["q1"]
