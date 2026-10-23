"""Routing 결과 계약 테스트.

- fixtures/routing/*.json 의 routing이 모두 스키마를 통과하고, 같은 이름의 분해 fixture와 하위 질문 id가 맞아야 한다
- 계약에서 금지한 형태는 스키마가 막아야 한다

    uv run python -m pytest -q
"""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from negopilot_shared import DecompositionResult, RouteResult, ToolCall, public_error_code

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"
ROUTING = sorted((FIXTURE_DIR / "routing").glob("*.json"))


def load(name: str) -> dict:
    path = next(p for p in ROUTING if p.stem.startswith(name))
    return json.loads(path.read_text(encoding="utf-8"))["routing"]


def test_every_decomposition_fixture_has_routing():
    assert [p.name for p in ROUTING] == sorted(p.name for p in (FIXTURE_DIR / "decomposition").glob("*.json"))


@pytest.mark.parametrize("path", ROUTING, ids=lambda p: p.stem)
def test_fixture_matches_contract_and_decomposition(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    routing = RouteResult.model_validate(data["routing"])
    decomp_data = json.loads((FIXTURE_DIR / data["decomposition"]).read_text(encoding="utf-8"))
    decomposition = DecompositionResult.model_validate(decomp_data["expected"])
    assert data["query"] == decomp_data["query"]
    assert [o.id for o in routing.outcomes] == [sq.id for sq in decomposition.sub_queries]
    assert [o.intent for o in routing.outcomes] == [sq.intent for sq in decomposition.sub_queries]
    assert (routing.status == "not_routed") == (decomposition.status != "ok")


def test_no_tool_statuses_are_split():
    assert load("06")["outcomes"][0]["status"] == "general_knowledge"
    assert load("10")["outcomes"][0]["status"] == "out_of_scope"


def test_law_refs_are_prefetched_in_fixture():
    outcome = load("05")["outcomes"][0]
    assert outcome["referenced_articles"]
    assert all(c["tool"] == "get_article" for c in outcome["referenced_articles"])


def broken(name: str, mutate) -> dict:
    data = copy.deepcopy(load(name))
    mutate(data)
    return data


@pytest.mark.parametrize("data, message", [
    (broken("06", lambda d: d["outcomes"][0].update(status="no_tool")), "status"),
    (broken("01", lambda d: d["outcomes"][0].update(calls=[])), "도구 호출이 1개 이상"),
    (broken("01", lambda d: d["outcomes"][0].update(missing=["law"])), "missing"),
    (broken("02", lambda d: d["outcomes"][1].update(id="q1")), "중복"),
    (broken("09", lambda d: d.update(outcomes=load("06")["outcomes"])), "not_routed"),
    (broken("01", lambda d: d.update(halted=True)), "halt_reason"),
    (broken("01", lambda d: d.update(halted=True, halt_reason="q1: 없음")), "empty"),
    (broken("01", lambda d: d.update(schema_version="0.2")), "schema_version"),
], ids=["unknown_status", "ok_without_calls", "missing_without_status", "duplicate_id",
        "not_routed_with_outcomes", "halted_without_reason", "halted_without_empty", "unknown_version"])
def test_contract_rejects(data, message):
    with pytest.raises(ValidationError, match=message):
        RouteResult.model_validate(data)


@pytest.mark.parametrize("message, code", [
    ("[RFQ_NOT_FOUND] RFQ 'RFQ-2026-099'가 없다", "RFQ_NOT_FOUND"),
    ("[VALUE_OUT_OF_RANGE] raw_material_ratio 는 0~1 사이", "VALUE_OUT_OF_RANGE"),
    ("[DB_UNAVAILABLE] 협력사 DB에 연결할 수 없다", None),      # 공개하지 않는 코드
    ("[INTERNAL] secret", None),
    ("RFQ 'RFQ-2026-099'가 없다", None),                        # 코드 없는 기존 메시지
    ("ToolError: [RFQ_NOT_FOUND] ...", None),                   # 맨 앞이 아니면 코드로 보지 않는다
])
def test_public_error_code(message, code):
    assert public_error_code(message) == code


@pytest.mark.parametrize("code", ["RFQ_NOT_FOUND", "SUPPLIER_NOT_FOUND", "SUPPLIER_AMBIGUOUS", "VALUE_OUT_OF_RANGE"])
def test_public_error_code_survives_http_and_stored_json(code):
    call = ToolCall(server="business-mcp", tool="check_offer", args={}, error="Tool execution failed.", error_code=code)
    assert call.model_dump()["error_code"] == code
    assert ToolCall.model_validate_json(call.model_dump_json()) == call


@pytest.mark.parametrize("error", [None, "Tool execution failed."])
def test_legacy_tool_calls_keep_their_five_field_shape(error):
    legacy = {"server": "business-mcp", "tool": "get_supplier", "args": {}, "result": None, "error": error}
    assert ToolCall.model_validate(legacy).model_dump() == legacy
    assert ToolCall.model_validate(legacy | {"error_code": None}).model_dump() == legacy


@pytest.mark.parametrize("code", ["DB_UNAVAILABLE", "INTERNAL", "secret://private", "", 123, True, {}, []])
def test_nonpublic_or_malformed_error_codes_are_rejected(code):
    with pytest.raises(ValidationError):
        ToolCall(server="business-mcp", tool="check_offer", args={}, error="x", error_code=code)


def test_successful_tool_cannot_claim_an_error_code():
    with pytest.raises(ValidationError, match="error_code"):
        ToolCall(server="business-mcp", tool="check_offer", args={}, error_code="RFQ_NOT_FOUND")
