"""금액 단위 보존 회귀. 모델과 MCP 및 DB를 호출하지 않는다."""

import json

import pytest

from negopilot_shared import DecompositionResult, RouteResult
from negopilot_agent.generation import AnswerGenerator
from negopilot_agent.generation import verify
from negopilot_agent.generation.answer import Evidence


def evidence(text: str) -> list[Evidence]:
    return [Evidence(id="E1", sub_query="q1", source="business", kind="supplier", label="재무 자료", text=text)]


@pytest.mark.parametrize("unit", ["조", "조원", "조 원", "억", "억원", "억 원", "만원", "만 원",
                                  "백만원", "천만원", "천억원", "경원"])
def test_money_units_cannot_reuse_an_unrelated_number(unit):
    report = verify.check(f"매출113.63{unit}입니다.", evidence('{"operating_margin":113.63}'))
    assert report.ungrounded_numbers == [f"113.63{unit}"]
    assert "113.63" not in verify.mask(f"매출113.63{unit}입니다.", report)


@pytest.mark.parametrize(("source", "answer"), [
    ('{"revenue":1136280172201}', "매출 113.63조입니다."),
    ('{"revenue":1136280172201}', "매출 1.136280172201조원입니다."),
    ('{"revenue":1136280172201}', "매출 11,362.80172201억원입니다."),
    ('{"revenue":1136280172201}', "매출 1,136,280,172,201억원입니다."),
    ('{"revenue_eok":10064}', "매출 10,064원입니다."),
    ('{"raw_material_ratio":0.45}', "금액 45원입니다."),
    ('{"bsns_year":2023}', "매출 2023원입니다."),
    ("매출 113.63억원", "매출 113.63조원입니다."),
    ("매출 113.63조원", "매출 113.63억원입니다."),
    ('{"net_income":-1000}', "순이익 1,000원입니다."),
])
def test_conversion_rounding_and_unit_or_sign_changes_are_rejected(source, answer):
    report = verify.check(answer, evidence(source))
    assert not report.ok
    assert report.ungrounded_numbers


@pytest.mark.parametrize(("source", "answer"), [
    ('{"financials":[{"revenue":1136280172201}]}', "매출 1,136,280,172,201원입니다."),
    ('{"revenue_eok":10064}', "매출 10,064억원입니다."),
    ('{"net_income":-1000}', "순이익 -1,000원입니다."),
    ('{"total_assets":9007199254740993}', "총자산 9,007,199,254,740,993원입니다."),
    ('{"unit_price":12000,"target_price":10000,"last_price":11000,"contract_amount":5000000}',
     "제안 12,000원과 목표 10,000원 및 직전 11,000원과 계약금액 5,000,000원입니다."),
    ("매출 113.630 억 원", "매출 113.63억원입니다."),
    ("매출 1.5조원", "매출 1.5조입니다."),
    ("매출 3백만 원", "매출 3백만원입니다."),
    ('{"revenue":1136280172201,"financials":[ …(생략)', "매출 1,136,280,172,201원입니다."),
])
def test_original_money_values_and_units_are_preserved(source, answer):
    assert verify.check(answer, evidence(source)).ok


def test_adjacent_large_integer_is_not_equal_after_float_rounding():
    report = verify.check("총자산 9,007,199,254,740,992원입니다.",
                          evidence('{"total_assets":9007199254740993}'))
    assert report.ungrounded_numbers == ["9,007,199,254,740,992원"]


def test_original_question_money_and_existing_ratio_and_days_remain_allowed():
    report = verify.check("단가 12,000원과 원재료 비중 45% 및 결제 75일입니다. [E1]",
                          evidence('{"unit_price":12000,"raw_material_ratio":0.45}'),
                          ["결제 75일과 계약금액 1억원을 검토해줘"])
    assert report.ok
    assert verify.check("계약금액 1억 원입니다.", [], ["계약금액 1억원을 검토해줘"]).ok
    assert verify.check("계약금액 1조원입니다.", [], ["계약금액 1억원을 검토해줘"]).ungrounded_numbers


def test_article_numbers_are_not_trillion_amounts():
    report = verify.check("하도급법 제13조와 제 16 조의 2를 확인했습니다.",
                          evidence("하도급법 제13조 및 제16조의2"))
    assert report.ok
    report = verify.check("하도급법 제99조를 확인했습니다.", evidence("하도급법 제13조"))
    assert report.ungrounded_numbers == []
    assert report.ungrounded_articles == ["제99조"]


class MoneyBackend:
    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.calls: list[str] = []

    async def generate(self, system: str, user: str) -> str:
        self.calls.append(user)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return json.dumps({"answer": answer, "used_evidence": ["E1"]}, ensure_ascii=False)


async def financial_answer(backend: MoneyBackend):
    decomposition = DecompositionResult.model_validate({
        "status": "ok", "original_query": "그 회사 재무 상태는?", "sub_queries": [{
            "id": "q1", "question": "SUP-0009 재무 상태 조회", "original_text": "그 회사 재무 상태는?",
            "intent": "supplier_detail", "grounding": "required", "confidence": 1,
            "entities": {"supplier": "SUP-0009"},
        }],
    })
    routing = RouteResult.model_validate({"status": "routed", "outcomes": [{
        "id": "q1", "intent": "supplier_detail", "status": "ok", "calls": [{
            "server": "business-mcp", "tool": "get_supplier", "args": {"supplier": "SUP-0009"},
            "result": {"supplier_id": "SUP-0009", "name": "검증 기업", "revenue_eok": 10064,
                       "financials": [{"bsns_year": 2023, "revenue": 1136280172201,
                                       "operating_income": 116628673833, "net_income": 86980060241}]},
        }],
    }]})
    return await AnswerGenerator(backend=backend).generate(decomposition, routing)


async def test_observed_trillion_conversion_is_retried_then_masked():
    backend = MoneyBackend("2023년 매출 113.63조와 영업이익 11.66조 및 순이익 8.70조입니다. [E1]")
    answer = await financial_answer(backend)
    assert len(backend.calls) == 2
    assert "113.63조" in backend.calls[1]
    assert answer.verification.masked
    assert not answer.verification.ok
    assert all(amount not in answer.text for amount in ("113.63조", "11.66조", "8.70조"))
    assert answer.text.count(verify.MASK) == 3
    assert answer.citations[0].evidence_id == "E1"


async def test_retry_can_copy_raw_won_amount_without_conversion():
    backend = MoneyBackend("2023년 매출 113.63조입니다. [E1]",
                           "2023년 매출 1,136,280,172,201원입니다. [E1]")
    answer = await financial_answer(backend)
    assert len(backend.calls) == 2
    assert answer.verification.ok and not answer.verification.masked
    assert "1,136,280,172,201원" in answer.text
