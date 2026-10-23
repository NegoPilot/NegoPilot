"""앞 결과에서 꺼낼 수 없는 inputs가 500으로 번지지 않는지 확인한다 (#179)."""

import json
from pathlib import Path

from negopilot_shared import DecompositionResult

from negopilot_agent.decomposition.decomposer import unreadable_inputs
from negopilot_agent.routing import Router

from tests.test_decomposition import FakeClaude, message_json
from tests.test_routing import FakeCaller

V24 = Path(__file__).resolve().parents[2] / "shared" / "fixtures" / "validation" / "v24_ranking_then_offer.json"


def expected() -> dict:
    return json.loads(V24.read_text(encoding="utf-8"))["expected"]


def with_bad_slot() -> dict:
    data = expected()
    data["sub_queries"][1]["inputs"][0]["slot"] = "rfq_id"
    return data


def test_valid_inputs_have_no_errors():
    assert unreadable_inputs(DecompositionResult.model_validate(expected())) == []


def test_slot_missing_from_result_map_is_reported():
    errors = unreadable_inputs(DecompositionResult.model_validate(with_bad_slot()))
    assert len(errors) == 1 and "q2" in errors[0] and "'rfq_id'" in errors[0]


async def test_decomposer_retries_unreadable_inputs():
    fake = FakeClaude(message_json(json.dumps(with_bad_slot(), ensure_ascii=False)),
                      message_json(json.dumps(expected(), ensure_ascii=False)))
    result = await fake.decomposer().decompose("RFQ-2026-002 1등 업체한테 단가 9,000원, 결제 60일로 제안해도 돼?")
    assert result.status == "ok"
    assert result.sub_queries[1].inputs[0].slot == "supplier"
    assert "꺼낼 수 없다" in fake.body(1)["messages"][0]["content"]


async def test_router_skips_unreadable_input_instead_of_raising():
    result = await Router(FakeCaller()).route(DecompositionResult.model_validate(with_bad_slot()))
    assert result.outcome("q1").status == "ok"
    assert result.outcome("q2").status == "skipped"
