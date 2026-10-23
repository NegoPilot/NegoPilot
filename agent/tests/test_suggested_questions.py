"""추천 질문은 기존 답변 생성 호출에 포함되며 본문의 검증 결과를 바꾸지 않는다."""

import json
from pathlib import Path

import pytest

from negopilot_shared import DecompositionResult, RouteResult
from negopilot_agent.generation import AnswerGenerator
from negopilot_agent.generation.answer import Answer, GenerationDraft
from negopilot_agent.generation.backends import (
    GenerationAnthropicBackend, GenerationOllamaBackend, GenerationOpenAIBackend,
)


FIXTURES = Path(__file__).resolve().parents[2] / "shared" / "fixtures"
FOLLOWUPS = [
    "RFQ에 어떤 항목을 작성하나요?",
    "협력사 정보를 어떻게 확인하나요?",
    "협력사의 재무 상태를 확인하려면 무엇이 필요한가요?",
    "협력사 순위를 조회하려면 무엇이 필요한가요?",
    "협상안을 검사하려면 어떤 정보가 필요한가요?",
]


def fixture(name="06_general_knowledge"):
    recorded = json.loads((FIXTURES / "routing" / f"{name}.json").read_text())
    decomposition = json.loads((FIXTURES / recorded["decomposition"]).read_text())
    return DecompositionResult.model_validate(decomposition["expected"]), RouteResult.model_validate(recorded["routing"])


class Backend:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def generate(self, system, user):
        self.calls.append((system, user))
        assert self.responses, "Unexpected extra model call"
        return self.responses.pop(0)


def draft(suggestions, answer="RFQ는 견적 요청서입니다."):
    return json.dumps({"answer": answer, "used_evidence": [], "suggested_questions": suggestions}, ensure_ascii=False)


async def generate(backend, name="06_general_knowledge"):
    decomposition, routing = fixture(name)
    return await AnswerGenerator(backend=backend).generate(decomposition, routing)


@pytest.mark.parametrize("questions", [[], FOLLOWUPS[:1], FOLLOWUPS])
async def test_zero_one_and_more_than_three_questions_share_the_original_model_call(questions):
    backend = Backend(draft(questions))
    answer = await generate(backend)
    assert answer.kind == "answer"
    assert answer.text == "RFQ는 견적 요청서입니다."
    assert answer.suggested_questions == questions
    assert answer.options == []
    assert answer.verification.ok and answer.verification.attempts == 1
    assert len(backend.calls) == 1
    assert "suggested_questions" in backend.calls[0][0]
    assert all(question not in answer.text for question in questions)


@pytest.mark.parametrize("invalid", [None, "질문", 4, True, {"question": "질문"}, [None, 3, False, {}]])
async def test_bad_optional_suggestion_field_does_not_retry_or_fail_the_answer(invalid):
    backend = Backend(draft(invalid))
    answer = await generate(backend)
    assert answer.kind == "answer" and answer.verification.ok
    assert answer.suggested_questions == []
    assert len(backend.calls) == 1


async def test_questions_are_trimmed_deduplicated_and_filtered_without_losing_valid_items():
    questions = ["  " + FOLLOWUPS[0] + "\n", FOLLOWUPS[0], " ", None, 13,
                 "질" * 121, "잘못된\0질문", FOLLOWUPS[1], *FOLLOWUPS[2:]]
    backend = Backend(draft(questions))
    answer = await generate(backend)
    assert answer.suggested_questions == FOLLOWUPS
    assert len(backend.calls) == 1


async def test_bom_and_python_whitespace_are_normalized_before_deduplication():
    boundary = "\ufeff \u0085\ufeff\u001c\t\ufeff\n"
    backend = Backend(draft([boundary + FOLLOWUPS[0] + boundary, FOLLOWUPS[0], boundary]))
    answer = await generate(backend)
    assert answer.suggested_questions == FOLLOWUPS[:1]
    assert answer.text == "RFQ는 견적 요청서입니다."
    assert len(backend.calls) == 1


def test_question_length_counts_unicode_codepoints_and_does_not_truncate():
    draft_model = GenerationDraft.model_validate({
        "answer": "정상 답변", "suggested_questions": ["😀" * 120, "😀" * 121],
    })
    assert draft_model.suggested_questions == ["😀" * 120]


async def test_old_drafts_and_saved_answers_without_the_field_remain_valid():
    backend = Backend(json.dumps({"answer": "RFQ는 견적 요청서입니다.", "used_evidence": []}))
    answer = await generate(backend)
    assert answer.suggested_questions == []
    previous = answer.model_dump(exclude={"suggested_questions"})
    assert Answer.model_validate(previous).suggested_questions == []
    assert len(backend.calls) == 1


@pytest.mark.parametrize("name", ["09_clarify", "10_out_of_scope", "11_failed"])
async def test_non_answer_outcomes_do_not_generate_suggestions_or_call_a_model(name):
    backend = Backend()
    answer = await generate(backend, name)
    assert answer.kind in {"clarify", "refusal", "failed"}
    assert answer.suggested_questions == []
    assert backend.calls == []


async def test_no_result_does_not_generate_suggestions_or_call_a_model():
    decomposition, routing = fixture("02_parallel_suppliers")
    routing = routing.model_copy(update={"outcomes": [
        outcome.model_copy(update={"status": "empty", "calls": []}) for outcome in routing.outcomes
    ]})
    backend = Backend()
    answer = await AnswerGenerator(backend=backend).generate(decomposition, routing)
    assert answer.kind == "no_result" and answer.suggested_questions == []
    assert backend.calls == []


async def test_partial_answer_waiting_for_information_keeps_options_and_hides_suggestions():
    decomposition, routing = fixture("02_parallel_suppliers")
    missing = routing.outcomes[1].model_copy(update={"status": "missing_args", "missing": ["supplier"], "calls": []})
    routing = routing.model_copy(update={"outcomes": [routing.outcomes[0], missing]})
    backend = Backend(draft(FOLLOWUPS, "확인된 자료를 정리했습니다. [E1]"))
    answer = await AnswerGenerator(backend=backend).generate(decomposition, routing)
    assert answer.kind == "answer" and answer.citations
    assert any("정보가 더 필요" in notice for notice in answer.notices)
    assert answer.suggested_questions == []
    assert len(backend.calls) == 1


async def test_invalid_suggestions_do_not_change_the_answer_citations_or_verification():
    backend = Backend(draft([
        "근거를 설명해 주세요. [E1]", "근거를 설명해 주세요. [1]",
        "99999999원 조건을 검사해 주세요.", "제99999조를 조회해 주세요.",
        "SUP-99999999의 재무 상태를 확인해 주세요.", "RFQ-2099-99999의 협력사 순위를 조회해 주세요.",
        FOLLOWUPS[0],
    ], "조회 근거를 안내합니다. [E1]"))
    answer = await generate(backend, "01_law_article")
    assert answer.text == "조회 근거를 안내합니다. [1]"
    assert answer.suggested_questions == FOLLOWUPS[:1]
    assert len(answer.citations) == 1 and answer.citations[0].evidence_id == "E1"
    assert answer.verification.ok and not answer.verification.masked
    assert len(backend.calls) == 1


async def test_known_ids_and_numbers_from_the_request_can_appear_in_questions():
    decomposition, routing = fixture()
    decomposition = decomposition.model_copy(update={"original_query": "SUP-0034와 RFQ-2026-001의 지급 45일 조건"})
    questions = ["SUP-0034의 재무 상태를 확인해 주세요.", "RFQ-2026-001의 협력사 순위를 조회해 주세요.",
                 "지급 45일 조건의 법적 기준을 알려 주세요."]
    backend = Backend(draft(questions))
    answer = await AnswerGenerator(backend=backend).generate(decomposition, routing)
    assert answer.suggested_questions == questions
    assert len(backend.calls) == 1


async def test_decomposition_assumptions_do_not_authorize_new_ids_or_numbers_in_suggestions():
    decomposition, routing = fixture()
    decomposition = decomposition.model_copy(update={"assumptions": ["SUP-9999의 지급 99일 조건으로 가정"]})
    backend = Backend(draft(["SUP-9999의 정보를 알려 주세요.", "지급 99일 조건의 법적 기준을 알려 주세요.", FOLLOWUPS[0]]))
    answer = await AnswerGenerator(backend=backend).generate(decomposition, routing)
    assert answer.suggested_questions == FOLLOWUPS[:1]
    assert len(backend.calls) == 1


async def test_masked_answer_never_publishes_suggestions():
    backend = Backend(draft(FOLLOWUPS, "확인되지 않은 금액은 99999999원입니다."),
                      draft(FOLLOWUPS, "확인되지 않은 금액은 99999999원입니다."))
    answer = await generate(backend)
    assert answer.kind == "answer" and answer.verification.masked
    assert answer.suggested_questions == []
    assert len(backend.calls) == 2


async def test_suggestions_come_from_the_final_successful_answer_draft():
    backend = Backend(draft(FOLLOWUPS[:1], "확인되지 않은 금액은 99999999원입니다."), draft(FOLLOWUPS[1:]))
    answer = await generate(backend)
    assert answer.verification.ok and answer.verification.attempts == 2
    assert answer.suggested_questions == FOLLOWUPS[1:]
    assert len(backend.calls) == 2


def test_provider_output_schemas_add_the_field_without_an_item_count_limit():
    schemas = [GenerationDraft.model_json_schema(), Answer.model_json_schema(),
               GenerationOpenAIBackend.OUTPUT_SCHEMA, GenerationOllamaBackend.OUTPUT_SCHEMA,
               GenerationAnthropicBackend.OUTPUT_FORMAT["schema"]]
    for schema in schemas:
        field = schema["properties"]["suggested_questions"]
        assert field["type"] == "array" and "maxItems" not in field and "minItems" not in field
        assert field["items"]["type"] == "string"
