"""openai 분해 백엔드의 짧은 출력 형식(wire.py) 테스트.

모든 개발용·검증용 fixture의 정답을 짧은 형식으로 바꿔 가짜 OpenAI 응답으로 주면, 분해기가 정답과 같은 계약 결과를 낸다.

    uv run python -m pytest -q tests/test_decomposition_wire.py
"""

import json
from pathlib import Path

import pytest

from negopilot_shared import DecompositionResult

from negopilot_agent.decomposition import wire
from negopilot_agent.decomposition.decomposer import named_law

from tests.test_decomposition import FakeOpenAI

FIXTURES = Path(__file__).resolve().parents[2] / "shared" / "fixtures"
CASES = sorted([*(FIXTURES / "decomposition").glob("*.json"), *(FIXTURES / "validation").glob("*.json")])


def to_wire(expected: dict) -> dict:
    """계약 형식 정답 → 모델이 낼 짧은 형식."""
    result = DecompositionResult.model_validate(expected).model_dump(mode="json", by_alias=True)
    del result["original_query"], result["schema_version"]
    for sq in result["sub_queries"]:
        sq["entities"] = [{"key": k, "value": v} for k, v in sq["entities"].items() if v is not None]
    return result


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
async def test_wire_output_becomes_contract_result(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    short = to_wire(data["expected"])
    wire.DecompositionWire.model_validate(short)   # 모델이 낼 수 있는 모양이다

    fake = FakeOpenAI(json.dumps(short, ensure_ascii=False))
    result = await fake.decomposer().decompose(data["query"])
    assert result == DecompositionResult.model_validate(data["expected"])
    assert len(fake.requests) == 1   # 형식 때문에 재시도하지 않는다


async def test_request_uses_short_schema_and_format_note():
    data = json.loads(CASES[0].read_text(encoding="utf-8"))
    fake = FakeOpenAI(json.dumps(to_wire(data["expected"]), ensure_ascii=False))
    await fake.decomposer().decompose(data["query"])

    body = json.loads(fake.requests[0].content)
    schema = body["response_format"]["json_schema"]["schema"]
    assert "original_query" not in schema["properties"] and "schema_version" not in schema["properties"]
    entities = schema["$defs"]["WireSubQuery"]["properties"]["entities"]
    assert entities["type"] == "array"
    assert wire.COMPACT_RULE in body["messages"][0]["content"]
    assert '"schema_version"' not in body["messages"][0]["content"]


def test_contract_shaped_or_broken_text_passes_through():
    contract = json.dumps({"status": "ok", "sub_queries": [{"entities": {"rfq_id": "RFQ-2026-001"}}]})
    assert json.loads(wire.to_contract_json(contract)) == json.loads(contract)
    assert wire.to_contract_json("{not json") == "{not json"


@pytest.mark.parametrize("text, law", [
    ("하도급법상 대금 지급 기한은 며칠이야?", "subcontract_act"),
    ("하도급법 시행령 제5조 보여 줘", "subcontract_decree"),
    ("상생협력법상 연동 약정 예외가 뭐야?", "winwin_act"),
    ("납품대금 연동제 적용 안 받으려면 어떻게 해야 해?", None),   # 하도급법에도 연동 조항이 있다 (#67)
    ("하도급법이랑 상생협력법 둘 다 봐 줘", None),
    ("2차 업체 단가 일괄로 깎아도 돼?", None),
])
def test_named_law(text, law):
    assert named_law(text) == law
