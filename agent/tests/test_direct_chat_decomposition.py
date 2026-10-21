"""Direct chat prompt and parsing regressions with fake model IO.

These tests verify prompt delivery and actual QueryDecomposer validation.
The supplied model outputs do not establish live model classification quality.
"""

from copy import deepcopy
from datetime import date
import json
import socket

import pytest

from negopilot_shared import DecompositionResult

from negopilot_agent.decomposition import QueryDecomposer
from negopilot_agent.decomposition.prompt import EXAMPLES, build_system_prompt, build_user_message


@pytest.fixture(autouse=True)
def prohibit_external_sockets(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Direct chat decomposition tests must not open sockets")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


class ScriptedBackend:
    def __init__(self, *outputs):
        self.outputs = list(deepcopy(outputs))
        self.calls = []

    async def generate(self, system, user):
        self.calls.append((system, user))
        return json.dumps(self.outputs.pop(0), ensure_ascii=False)


def example(query):
    return deepcopy(next(item for item in EXAMPLES if item["query"] == query))


def task_result(query, intent, question, entities=None):
    return {
        "status": "ok", "original_query": query,
        "sub_queries": [{
            "id": "q1", "question": question, "original_text": query, "intent": intent,
            "grounding": "none" if intent == "general_knowledge" else "required",
            "confidence": 0.9, "entities": entities or {},
        }],
    }


def clarification(query, question, options):
    return {
        "status": "clarify", "original_query": query,
        "clarification": {"reason": "실행에 필요한 정보가 없음", "question": question, "options": options},
    }


def test_prompt_distinguishes_legal_conditions_from_concrete_offer_inspection():
    system = build_system_prompt()
    assert "숫자가 있다는 이유만으로 offer_check를 고르지 않는다" in system
    assert "일반적인 행위나 조건의 법적 허용 여부는 숫자가 있어도 law_check" in system
    assert "조건은 question에 보존한다" in system
    assert "supplier와 rfq_id 및 unit_price와 payment_days 및 price_linkage가 필요" in system
    assert "없는 항목만 되묻는다" in system
    assert "검사 목적을 유지한다" in system
    assert "구체적 숫자 협상안이 없을 때" not in system


def test_prompt_does_not_require_company_or_optional_search_conditions():
    system = build_system_prompt()
    assert "기업 선택은 대화를 시작하는 필수 조건이 아니다" in system
    assert "supplier_search의 process는 선택 조건" in system
    assert "조건 없는 협력사 목록 요청도 supplier_search" in system
    assert "법령 이름이나 조문 번호가 없어도 law_check" in system
    assert "이미 주어진 조건은 다시 묻지 않는다" in system


def test_prompt_requires_grounded_continuations_and_respects_new_topics():
    system = build_system_prompt()
    assert "원래 사용자의 작업과 목적 및 이미 준 조건을 함께 해석" in system
    assert "선택지가 이력에 실제로 있을 때만" in system
    assert "선택지가 없거나 번호가 범위를 벗어나면" in system
    assert "복원한 작업의 목적과 대상 및 주어진 조건을 모두 담는다" in system
    assert "original_query는 현재 입력 그대로 유지한다" in system
    assert "새로운 명시적 질문이면 그 질문을 우선" in system
    assert "현재 입력에서 명시적으로 바꾼 대상이나 조건은 이전 값보다 우선" in system


@pytest.mark.parametrize("query", [
    "거래 가능한 협력사 목록을 보여줘",
    "결제 90일로 해도 돼?",
    "단가를 5% 내려도 돼?",
    "SUP-0027의 RFQ-2026-018 협상안을 단가 8700원과 지급 45일로 규칙 검사해줘",
    "포함하지 않을게",
    "2번",
    "협력사 품질 감사가 뭔지 설명해줘",
])
async def test_direct_chat_examples_reach_actual_decomposer_without_rewriting_inputs(query):
    case = example(query)
    expected = DecompositionResult.model_validate(case["result"])
    backend = ScriptedBackend(case["result"] | {"original_query": "모델이 변경한 질문"})
    history = case.get("history", [])
    context = {"selected_supplier_ids": []}
    snapshot = deepcopy((history, context))

    result = await QueryDecomposer(backend=backend).decompose(query, history, context=context)

    assert result == expected
    assert len(backend.calls) == 1
    system, user = backend.calls[0]
    assert system == build_system_prompt()
    assert user.endswith(f"분해할 질문:\n{query}")
    assert all(turn["content"] in user for turn in history)
    assert (history, context) == snapshot
    assert result.original_query == query


@pytest.mark.parametrize(("query", "intent", "question", "entities"), [
    ("견적 요청서의 목적이 뭐야?", "general_knowledge", "견적 요청서의 개념과 목적", {}),
    ("충남 협력사를 찾아줘", "supplier_search", "충남 지역 협력사 후보 조회", {"region": "충남"}),
    ("대금 지급에 관한 법 내용을 찾아줘", "law_check", "하도급대금 지급에 관한 법령 검색", {}),
    ("가상금속의 재무 정보를 알려줘", "supplier_detail", "가상금속 협력사의 재무 정보 조회", {"supplier": "가상금속"}),
])
async def test_no_selected_company_is_required_for_complete_requests(query, intent, question, entities):
    backend = ScriptedBackend(task_result(query, intent, question, entities))
    result = await QueryDecomposer(backend=backend).decompose(query, context={"selected_supplier_ids": []})
    assert result.status == "ok"
    assert result.sub_queries[0].intent == intent
    assert result.sub_queries[0].entities.model_dump(exclude_none=True) == entities


@pytest.mark.parametrize("query", ["결제 90일로 해도 돼?", "단가를 5% 내려도 돼?"])
async def test_numeric_legal_conditions_remain_in_question_without_invalid_entity_slots(query):
    expected = example(query)["result"]
    backend = ScriptedBackend(expected)
    result = await QueryDecomposer(backend=backend).decompose(query, context={"selected_supplier_ids": []})
    subquery = result.sub_queries[0]
    assert subquery.intent == "law_check"
    assert ("90일" if "90" in query else "5%") in subquery.question
    assert subquery.entities.model_dump(exclude_none=True) == {}


async def test_wrong_legal_entity_slot_is_still_rejected_then_retried():
    correct = example("결제 90일로 해도 돼?")["result"]
    invalid = deepcopy(correct)
    invalid["sub_queries"][0]["entities"] = {"payment_days": 90}
    backend = ScriptedBackend(invalid, correct)
    result = await QueryDecomposer(backend=backend).decompose("결제 90일로 해도 돼?")
    assert len(backend.calls) == 2
    assert "계약 형식을 어겼다" in backend.calls[1][1]
    assert result.sub_queries[0].intent == "law_check"
    assert result.sub_queries[0].entities.payment_days is None


async def test_answerable_part_and_identified_incomplete_part_remain_separate_subqueries():
    query = "대금 90일 지급의 법적 기준을 알려주고 SUP-0027의 RFQ-2026-018 협상안을 단가 8700원과 지급 45일로 검사해줘"
    backend = ScriptedBackend(example(query)["result"])

    result = await QueryDecomposer(backend=backend).decompose(query, context={"selected_supplier_ids": []})

    assert result.status == "ok"
    assert result.clarification is None
    assert result.original_query == query
    legal, offer = result.sub_queries
    assert legal.intent == "law_check" and "90일" in legal.question
    assert legal.entities.model_dump(exclude_none=True) == {}
    assert offer.intent == "offer_check"
    assert offer.entities.model_dump(exclude_none=True) == {
        "supplier": "SUP-0027", "rfq_id": "RFQ-2026-018", "unit_price": 8700, "payment_days": 45,
    }
    assert offer.entities.price_linkage is None
    assert result.parallel_groups() == [["q1", "q2"]]
    assert "필수 인자만 부족하면 status는 ok" in backend.calls[0][0]
    assert "Generation이 가능한 부분을 답하면서 빠진 정보만 묻는다" in backend.calls[0][0]


async def test_clarification_then_option_reply_preserves_original_task_and_current_query():
    original = "이 업체의 재무 상태를 알려줘"
    question = "어느 업체인가요?"
    options = ["가상금속 (SUP-0097)", "가상정밀 (SUP-0088)"]
    followup = example("2번")["result"]
    backend = ScriptedBackend(clarification(original, question, options), followup)
    decomposer = QueryDecomposer(backend=backend)
    context = {"selected_supplier_ids": []}

    first = await decomposer.decompose(original, context=context)
    assert first.status == "clarify" and first.sub_queries == []
    history = [
        {"role": "user", "content": original},
        {"role": "assistant", "content": question + "\n1. " + options[0] + "\n2. " + options[1]},
    ]
    snapshot = deepcopy(history)
    second = await decomposer.decompose("2번", history, context=context)

    assert second.status == "ok"
    assert second.original_query == second.sub_queries[0].original_text == "2번"
    assert second.sub_queries[0].intent == "supplier_detail"
    assert second.sub_queries[0].entities.supplier == "SUP-0088"
    assert "재무 상태" in second.sub_queries[0].question
    assert original in backend.calls[1][1]
    assert all(option in backend.calls[1][1] for option in options)
    assert history == snapshot


@pytest.mark.parametrize(("reply", "question"), [
    ("2번", "어느 업체인가요?"),
    ("4번", "어느 업체인가요?\n1. 가상금속 (SUP-0097)\n2. 가상정밀 (SUP-0088)"),
])
async def test_unresolvable_option_reply_can_request_only_missing_context(reply, question):
    history = [{"role": "assistant", "content": question}]
    backend = ScriptedBackend(clarification(
        reply, "업체 이름이나 ID를 알려 주시겠어요?", ["업체 이름 입력", "업체 ID 입력"],
    ))
    result = await QueryDecomposer(backend=backend).decompose(reply, history, context={"selected_supplier_ids": []})
    assert result.status == "clarify"
    assert result.sub_queries == []
    assert result.original_query == reply
    assert "선택지가 이력에 실제로 있을 때만" in backend.calls[0][0]


async def test_new_explicit_question_is_preserved_after_a_clarification():
    case = example("협력사 품질 감사가 뭔지 설명해줘")
    backend = ScriptedBackend(case["result"])
    result = await QueryDecomposer(backend=backend).decompose(case["query"], case["history"])
    assert result.status == "ok"
    assert result.original_query == case["query"]
    assert result.sub_queries[0].intent == "general_knowledge"
    assert result.sub_queries[0].entities.model_dump(exclude_none=True) == {}
    assert "재무" not in result.sub_queries[0].question


def test_history_preserves_original_task_beyond_six_messages():
    history = [{"role": "user", "content": "원래 협상안을 규칙 검사해줘"}]
    history.extend({"role": "assistant" if i % 2 else "user", "content": f"추가 조건 확인 {i}"} for i in range(7))
    snapshot = deepcopy(history)
    prompt = build_user_message("연동 약정은 포함하지 않아", history, date(2026, 10, 21))
    assert all(turn["content"] in prompt for turn in history)
    assert "대상을 찾을 때만 참고" not in prompt
    assert history == snapshot


def test_history_is_capped_at_twenty_without_mutating_source():
    history = [{"role": "user", "content": f"message-{i:02d}-end"} for i in range(21)]
    snapshot = deepcopy(history)
    prompt = build_user_message("현재 입력", history, date(2026, 10, 21))
    assert history[0]["content"] not in prompt
    assert all(turn["content"] in prompt for turn in history[1:])
    assert prompt.count("사용자: message-") == 20
    assert prompt.endswith("분해할 질문:\n현재 입력")
    assert history == snapshot
