"""Native Ollama HTTP tests. No model process or external socket is used."""

import asyncio
import json
from pathlib import Path
import socket
from uuid import uuid4

import httpx2 as httpx
import pytest

from negopilot_shared import DecompositionResult
from negopilot_agent import ollama
from negopilot_agent.api.app import create_app
from negopilot_agent.api.generation_stubs import GenerationStubToolCaller
from negopilot_agent.api.settings import ApiSettings
from negopilot_agent.decomposition import DecomposerSettings
from negopilot_agent.generation import GeneratorSettings
from negopilot_agent.generation.answer import GenerationDraft
from negopilot_agent.generation.backends import GenerationOllamaBackend


ORIGINAL_CLIENT = httpx.AsyncClient
SECRET = "private-model-diagnostic"
FIXTURE = json.loads((Path(__file__).resolve().parents[2] / "shared/fixtures/decomposition/01_law_article.json").read_text())


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for name in ("OLLAMA_BASE_URL", "OLLAMA_TIMEOUT_SECONDS", "OLLAMA_NUM_CTX", "OLLAMA_NUM_PREDICT",
                 "NEGOPILOT_DECOMPOSER_MODEL", "NEGOPILOT_GENERATOR_MODEL"):
        monkeypatch.delenv(name, raising=False)
    def blocked(*args, **kwargs):
        pytest.fail("Ollama tests must not open a real socket")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


def completed(content):
    return {"message": {"role": "assistant", "content": content}, "done": True, "done_reason": "stop"}


@pytest.mark.parametrize(("backend_type", "schema"), [
    (ollama.OllamaBackend, DecompositionResult.model_json_schema()),
    (GenerationOllamaBackend, GenerationDraft.model_json_schema()),
])
async def test_native_request_uses_schema_without_changing_prompts(backend_type, schema):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=completed('{"result":"raw JSON text"}'))
    async with ORIGINAL_CLIENT(transport=httpx.MockTransport(handle), base_url="http://127.0.0.1:11434/") as client:
        backend = backend_type(client, "qwen3:8b", 1234)
        assert await backend.generate("system prompt", "user prompt") == '{"result":"raw JSON text"}'
    assert len(calls) == 1
    assert calls[0].url.path == "/api/chat"
    assert "authorization" not in calls[0].headers and "x-api-key" not in calls[0].headers
    assert json.loads(calls[0].content) == {
        "model": "qwen3:8b", "messages": [{"role": "system", "content": "system prompt"},
                                           {"role": "user", "content": "user prompt"}],
        "format": ollama.ollama_json_schema(schema), "stream": False, "think": False,
        "options": {"temperature": 0, "num_ctx": 32768, "num_predict": 1234},
    }


@pytest.mark.parametrize("payload", [[], {"error": SECRET}, {"done": False},
                                      {"done": True, "message": {"role": "user", "content": "x"}},
                                      {"done": True, "message": {"role": "assistant", "content": None}}])
async def test_malformed_envelopes_raise_safe_transport_error(payload):
    async with ORIGINAL_CLIENT(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)),
                               base_url="http://127.0.0.1:11434/") as client:
        with pytest.raises(httpx.RemoteProtocolError, match="Invalid Ollama response") as failure:
            await ollama.OllamaBackend(client, "qwen3:8b", 16000).generate("system", "user")
    assert SECRET not in str(failure.value)


def install_transport(monkeypatch, *, failure=None, failed_phase="decomposition"):
    calls, clients = [], []
    async def handle(request):
        data = json.loads(request.content)
        calls.append(data)
        phase = "generation" if data["format"].get("title") == "GenerationDraft" else "decomposition"
        if phase == failed_phase:
            if failure == "timeout":
                raise httpx.ReadTimeout(SECRET, request=request)
            if failure == "cancel":
                await asyncio.sleep(10)
            if failure == "network":
                raise httpx.ConnectError(SECRET, request=request)
            if failure == "http":
                return httpx.Response(500, json={"error": SECRET})
            if failure == "redirect":
                return httpx.Response(307, headers={"Location": "https://private.invalid"})
            if failure == "malformed":
                return httpx.Response(200, text=SECRET)
            if failure == "length":
                return httpx.Response(200, json={"done": True, "done_reason": "length"})
        content = json.dumps(FIXTURE["expected"]) if phase == "decomposition" else json.dumps(
            {"answer": "확인한 자료입니다. [E1]", "used_evidence": ["E1"]}, ensure_ascii=False)
        return httpx.Response(200, json=completed(content))

    class Client(ORIGINAL_CLIENT):
        def __init__(self, **kwargs):
            self.options = kwargs.copy()
            self.closes = 0
            super().__init__(transport=httpx.MockTransport(handle), **kwargs)
            clients.append(self)
        async def aclose(self):
            self.closes += 1
            await super().aclose()
    monkeypatch.setattr(ollama.httpx2, "AsyncClient", Client)
    monkeypatch.setenv("NEGOPILOT_DECOMPOSER_BACKEND", "ollama")
    monkeypatch.setenv("NEGOPILOT_GENERATOR_BACKEND", "ollama")
    return calls, clients


async def execute(*, timeout=30):
    app = create_app(settings=ApiSettings(mode="live", internal_api_key="test-key", request_timeout_seconds=timeout),
                     generation_caller=GenerationStubToolCaller())
    async with ORIGINAL_CLIENT(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/internal/v2/query-executions", headers={"X-Internal-Api-Key": "test-key"},
                                 json={"request_id": str(uuid4()), "query": FIXTURE["query"]})


async def test_http_graph_selects_native_backend_and_closes_owned_clients(monkeypatch):
    calls, clients = install_transport(monkeypatch)
    assert clients == []
    response = await execute()
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "live" and response.json()["execution_status"] == "COMPLETED"
    assert len(calls) == len(clients) == 2
    assert all(client.is_closed and client.closes == 1 for client in clients)
    assert all(client.options["trust_env"] is False and client.options["follow_redirects"] is False for client in clients)
    assert all(call["model"] == "qwen3:8b" and call["options"]["num_predict"] == 4096 for call in calls)
    assert calls[-1]["format"] == GenerationDraft.model_json_schema()
    assert response.json()["answer"]["kind"] == "answer"
    assert response.json()["answer"]["citations"]


@pytest.mark.parametrize("phase", ["decomposition", "generation"])
@pytest.mark.parametrize(("failure", "status", "code"), [
    ("timeout", 504, "UPSTREAM_TIMEOUT"), ("cancel", 504, "UPSTREAM_TIMEOUT"),
    ("network", 503, "UPSTREAM_UNAVAILABLE"), ("http", 503, "UPSTREAM_UNAVAILABLE"),
    ("redirect", 503, "UPSTREAM_UNAVAILABLE"), ("malformed", 503, "UPSTREAM_UNAVAILABLE"),
])
async def test_transport_failure_calls_each_phase_once_without_stub_fallback(monkeypatch, phase, failure, status, code, caplog):
    calls, clients = install_transport(monkeypatch, failure=failure, failed_phase=phase)
    response = await execute(timeout=0.05 if failure == "cancel" else 30)
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert len(calls) == (2 if phase == "generation" else 1)
    assert all(client.closes == 1 and client.is_closed for client in clients)
    assert SECRET not in response.text + caplog.text


@pytest.mark.parametrize("phase", ["decomposition", "generation"])
async def test_truncated_output_is_failed_without_a_second_attempt(monkeypatch, phase):
    calls, clients = install_transport(monkeypatch, failure="length", failed_phase=phase)
    response = await execute()
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "live" and response.json()["execution_status"] == "FAILED"
    assert response.json()["error"]["code"] == ("DECOMPOSITION_FAILED" if phase == "decomposition" else "GENERATION_FAILED")
    assert len(calls) == (1 if phase == "decomposition" else 2)
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize(("name", "value"), [
    ("OLLAMA_BASE_URL", "file:///tmp/model"), ("OLLAMA_BASE_URL", "http://user:secret@localhost:11434"),
    ("OLLAMA_BASE_URL", "http://localhost:11434/api?secret=x"), ("OLLAMA_BASE_URL", "http://localhost:bad"),
    ("OLLAMA_TIMEOUT_SECONDS", "nan"), ("OLLAMA_TIMEOUT_SECONDS", "0"),
    ("OLLAMA_NUM_CTX", "0"), ("OLLAMA_NUM_PREDICT", "-1"),
])
async def test_bad_configuration_creates_no_client_and_returns_503(monkeypatch, name, value):
    calls, clients = install_transport(monkeypatch)
    monkeypatch.setenv(name, value)
    response = await execute()
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    assert clients == calls == []


async def test_ollama_limits_and_origin_are_configurable(monkeypatch):
    calls, clients = install_transport(monkeypatch)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11435/")
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "123.5")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "40960")
    monkeypatch.setenv("OLLAMA_NUM_PREDICT", "2048")
    response = await execute()
    assert response.status_code == 200, response.text
    assert all(str(client.base_url) == "http://localhost:11435/" for client in clients)
    assert all(client.timeout.read == 123.5 and client.timeout.connect == 5 for client in clients)
    assert all(call["options"] == {"temperature": 0, "num_ctx": 40960, "num_predict": 2048} for call in calls)


def test_default_backend_is_still_openai(monkeypatch):
    monkeypatch.delenv("NEGOPILOT_DECOMPOSER_BACKEND", raising=False)
    monkeypatch.delenv("NEGOPILOT_GENERATOR_BACKEND", raising=False)
    assert DecomposerSettings().backend == GeneratorSettings().backend == "openai"
    assert DecomposerSettings().model == GeneratorSettings().model == "gpt-4.1-mini"


def test_grammar_compatibility_changes_only_digit_patterns_without_mutating_contract():
    original = DecompositionResult.model_json_schema()
    encoded = json.dumps(original, ensure_ascii=False)
    converted = ollama.ollama_json_schema(original)
    assert json.dumps(original, ensure_ascii=False) == encoded
    assert converted["$defs"]["SubQuery"]["properties"]["id"]["pattern"] == "^q[0-9]+$"
    entities = converted["$defs"]["Entities"]["properties"]
    assert entities["rfq_id"]["anyOf"][0]["pattern"] == "^RFQ-[0-9]{4}-[0-9]{3}$"
    assert entities["article_no"]["anyOf"][0]["pattern"] == "^제[0-9]+조(의[0-9]+)?$"
    assert entities["as_of"]["anyOf"][0]["pattern"] == "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
    assert entities["top_n"] == original["$defs"]["Entities"]["properties"]["top_n"]
    assert entities["raw_material_ratio"] == original["$defs"]["Entities"]["properties"]["raw_material_ratio"]
    assert converted["properties"]["clarification"] == original["properties"]["clarification"]
    assert ollama.ollama_json_schema(GenerationDraft.model_json_schema()) == GenerationDraft.model_json_schema()


@pytest.mark.parametrize(("pattern", "expected"), [
    (r"^\d+$", r"^[0-9]+$"), (r"^[\d-]+$", r"^[0-9-]+$"),
    (r"^\\d+$", r"^\\d+$"), (r"^\[\d\]$", r"^\[[0-9]\]$"),
])
def test_digit_expansion_respects_character_classes_and_escaped_literals(pattern, expected):
    assert ollama.ollama_json_schema({"pattern": pattern}) == {"pattern": expected}
