"""Ollama schema compatibility without model or network connections."""

from copy import deepcopy
import json
import socket

import anthropic
import httpx2
from jsonschema import Draft202012Validator
from openai.lib._pydantic import to_strict_json_schema
from pydantic import ValidationError
import pytest

from negopilot_shared import DecompositionResult

from negopilot_agent.decomposition import QueryDecomposer
from negopilot_agent.decomposition.backends import (
    AnthropicBackend,
    OllamaBackend,
    OpenAIBackend,
    inline_refs,
    prepare_ollama_schema,
)
from negopilot_agent.generation.answer import GenerationDraft
from negopilot_agent.generation.backends import (
    GenerationAnthropicBackend,
    GenerationOllamaBackend,
    GenerationOpenAIBackend,
)


PATTERNS = {
    "id": (r"^q\d+$", r"^q[0-9]+$"),
    "rfq_id": (r"^RFQ-\d{4}-\d{3}$", r"^RFQ-[0-9]{4}-[0-9]{3}$"),
    "article_no": (r"^제\d+조(의\d+)?$", r"^제[0-9]+조(의[0-9]+)?$"),
    "as_of": (r"^\d{4}-\d{2}-\d{2}$", r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"),
}
VALID_VALUES = {"id": "q12", "rfq_id": "RFQ-2026-001", "article_no": "제13조의2", "as_of": "2026-10-21"}
UNICODE_VALUES = {"id": "q１２", "rfq_id": "RFQ-２０２６-００１", "article_no": "제１３조의２", "as_of": "２０２６-１０-２１"}
INVALID_VALUES = {"id": "query1", "rfq_id": "RFQ-2026-1", "article_no": "13조", "as_of": "2026-1-2"}


@pytest.fixture(autouse=True)
def prohibit_external_sockets(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Ollama schema tests must not open sockets")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


def all_nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from all_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from all_nodes(child)


def field_schema(schema, field):
    properties = schema["properties"]["sub_queries"]["items"]["properties"]
    if field == "id":
        return properties[field]
    return properties["entities"]["properties"][field]


def string_pattern(schema):
    return next(node["pattern"] for node in all_nodes(schema) if "pattern" in node)


def decomposition_with(field, value):
    intent = "supplier_ranking" if field == "rfq_id" else "law_article"
    entities = {"rfq_id": "RFQ-2026-001"} if field == "rfq_id" else {
        "law": "subcontract_act", "article_no": "제13조", "as_of": "2025-01-01",
    }
    query = {
        "id": "q1", "question": "검증 질문", "original_text": "원문 질문",
        "intent": intent, "grounding": "required", "confidence": 0.9, "entities": entities,
    }
    if field == "id":
        query["id"] = value
    else:
        entities[field] = value
    return {"status": "ok", "original_query": "원문 질문", "sub_queries": [query]}


def test_preparation_inlines_references_on_independent_copies():
    source = {
        "$defs": {"Id": {"type": "string", "pattern": PATTERNS["id"][0]}},
        "type": "object",
        "properties": {
            "first": {"$ref": "#/$defs/Id", "description": "first ID"},
            "second": {"anyOf": [{"$ref": "#/$defs/Id"}, {"type": "null"}]},
        },
        "required": ["first"],
    }
    snapshot = deepcopy(source)
    prepared = prepare_ollama_schema(source)
    assert source == snapshot
    assert prepared is not source
    assert all("$ref" not in node and "$defs" not in node for node in all_nodes(prepared))
    assert prepared["properties"]["first"] == {
        "type": "string", "pattern": PATTERNS["id"][1], "description": "first ID",
    }
    assert prepared["properties"]["second"]["anyOf"][0]["pattern"] == PATTERNS["id"][1]
    prepared["properties"]["first"]["pattern"] = "changed"
    prepared["required"].append("second")
    assert source == snapshot
    assert prepared["properties"]["second"]["anyOf"][0]["pattern"] == PATTERNS["id"][1]


@pytest.mark.parametrize("field", PATTERNS)
def test_four_ollama_fields_require_ascii_digits(field):
    schema = OllamaBackend.OUTPUT_SCHEMA
    Draft202012Validator.check_schema(schema)
    selected = field_schema(schema, field)
    assert string_pattern(selected) == PATTERNS[field][1]
    validator = Draft202012Validator(selected)
    assert validator.is_valid(VALID_VALUES[field])
    assert not validator.is_valid(UNICODE_VALUES[field])
    assert not validator.is_valid(INVALID_VALUES[field])
    if field != "id":
        assert validator.is_valid(None)


@pytest.mark.parametrize("pattern", [
    r"^\d+$", r"^prefix-\d{2}$", r"^[A-Z]\w+$", r"^q[0-9]+$", r"^q\d{2}$", r"literal\\d",
])
def test_patterns_outside_the_explicit_compatibility_mapping_are_preserved(pattern):
    source = {"type": "string", "pattern": pattern, "description": PATTERNS["id"][0]}
    assert prepare_ollama_schema(source) == source


def test_only_pattern_values_change_in_the_full_shared_schema():
    source = DecompositionResult.model_json_schema()
    snapshot = deepcopy(source)
    prepared = prepare_ollama_schema(source)
    flattened = inline_refs(source)
    rewritten = deepcopy(prepared)
    reverse = {replacement: original for original, replacement in PATTERNS.values()}
    for node in all_nodes(rewritten):
        if node.get("pattern") in reverse:
            node["pattern"] = reverse[node["pattern"]]
    assert rewritten == flattened
    assert source == snapshot
    assert all("$ref" not in node and "$defs" not in node for node in all_nodes(prepared))


def test_shared_anthropic_and_openai_schemas_keep_their_original_patterns():
    source = DecompositionResult.model_json_schema()
    prepare_ollama_schema(source)
    flattened = inline_refs(DecompositionResult.model_json_schema())
    for field, (original, _) in PATTERNS.items():
        assert string_pattern(field_schema(flattened, field)) == original
    assert AnthropicBackend.OUTPUT_FORMAT == {
        "type": "json_schema", "schema": anthropic.transform_schema(source),
    }
    assert OpenAIBackend.OUTPUT_SCHEMA == to_strict_json_schema(DecompositionResult)
    assert GenerationAnthropicBackend.OUTPUT_FORMAT == {
        "type": "json_schema", "schema": anthropic.transform_schema(GenerationDraft.model_json_schema()),
    }
    assert GenerationOpenAIBackend.OUTPUT_SCHEMA == to_strict_json_schema(GenerationDraft)


@pytest.mark.parametrize("field", PATTERNS)
async def test_invalid_model_values_still_fail_core_postvalidation(field):
    invalid = decomposition_with(field, INVALID_VALUES[field])
    with pytest.raises(ValidationError):
        DecompositionResult.model_validate(invalid)
    sent = []

    async def handle(request):
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json={"message": {"content": json.dumps(invalid)}, "done_reason": "stop"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        backend = OllamaBackend("fake-ollama-model", base_url="http://ollama.invalid", http_client=client)
        result = await QueryDecomposer(backend=backend).decompose("원문 질문")
    assert result.status == "failed"
    assert result.sub_queries == []
    assert len(sent) == 2
    assert all(string_pattern(field_schema(body["format"], field)) == PATTERNS[field][1] for body in sent)


@pytest.mark.parametrize("draft", [
    {"answer": "근거 답변입니다. [E1]", "used_evidence": ["E1"]},
    {"answer": "숫자 １２와 조문 제１３조는 초안 문자열입니다."},
    {"answer": "", "used_evidence": []},
])
def test_generation_draft_meaning_and_defaults_remain_unchanged(draft):
    original_schema = GenerationDraft.model_json_schema()
    assert GenerationOllamaBackend.OUTPUT_SCHEMA == inline_refs(original_schema)
    assert Draft202012Validator(GenerationOllamaBackend.OUTPUT_SCHEMA).is_valid(draft)
    result = GenerationDraft.model_validate(draft)
    assert result.answer == draft["answer"]
    assert result.used_evidence == draft.get("used_evidence", [])


@pytest.mark.parametrize("draft", [
    {"used_evidence": []}, {"answer": None}, {"answer": 123}, {"answer": "본문", "used_evidence": "E1"},
])
def test_generation_draft_still_rejects_invalid_shapes(draft):
    assert not Draft202012Validator(GenerationOllamaBackend.OUTPUT_SCHEMA).is_valid(draft)
    with pytest.raises(ValidationError):
        GenerationDraft.model_validate(draft)


@pytest.mark.parametrize("backend_type", [OllamaBackend, GenerationOllamaBackend])
async def test_actual_ollama_format_payload_contains_prepared_schema(backend_type):
    draft = ({"answer": "그대로 보존할 모델 초안입니다. [E1]", "used_evidence": ["E1"]}
             if backend_type is GenerationOllamaBackend else decomposition_with("article_no", "제13조의2"))
    text = json.dumps(draft, ensure_ascii=False)
    sent = []

    async def handle(request):
        assert str(request.url) == "http://ollama.invalid/api/chat"
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json={"message": {"content": text}, "done_reason": "stop"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        backend = backend_type("fake-ollama-model", base_url="http://ollama.invalid/", http_client=client)
        assert await backend.generate("시스템 프롬프트", "사용자 원문") == text
    assert len(sent) == 1
    payload = sent[0]
    assert payload["model"] == "fake-ollama-model"
    assert payload["messages"] == [
        {"role": "system", "content": "시스템 프롬프트"}, {"role": "user", "content": "사용자 원문"},
    ]
    assert payload["stream"] is False
    assert payload["format"] == backend_type.OUTPUT_SCHEMA
    assert all("$ref" not in node and "$defs" not in node for node in all_nodes(payload["format"]))
    assert Draft202012Validator(payload["format"]).is_valid(draft)
    if backend_type is OllamaBackend:
        for field, (_, replacement) in PATTERNS.items():
            assert string_pattern(field_schema(payload["format"], field)) == replacement
