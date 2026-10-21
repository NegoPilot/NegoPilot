"""비율 필드를 %로 쓴 답변의 근거 검사 회귀. 모델과 MCP 및 DB를 호출하지 않는다.

부채비율(debt_ratio)은 1.6처럼 1을 넘는 값이라, 0~1 비율만 %로 바꿔 비교하던 검사가
"부채비율 160%"를 근거 없는 숫자로 보고 재생성한 뒤 [확인 필요]로 가렸다.
"""

import json
from pathlib import Path

import pytest

from negopilot_shared import DecompositionResult, RouteResult

from negopilot_agent.generation import AnswerGenerator, verify
from negopilot_agent.generation.answer import Evidence

FIXTURES = Path(__file__).resolve().parents[2] / "shared" / "fixtures"


def evidence(text: str) -> list[Evidence]:
    return [Evidence(id="E1", sub_query="q1", source="business", kind="supplier", label="협력사 정보", text=text)]


@pytest.mark.parametrize(("source", "answer"), [
    ('{"debt_ratio":1.6}', "부채비율은 160%입니다. [E1]"),
    ('{"debt_ratio":2.33}', "부채비율은 233%입니다. [E1]"),
    ('{"debt_ratio":1.852}', "부채비율은 185.2%입니다. [E1]"),
    ('{"debt_ratio":1.852}', "부채비율은 185%입니다. [E1]"),          # 표시용 반올림
    ('{"debt_ratio":1.852}', "부채비율은 1.852배입니다. [E1]"),        # 원래 단위
    ('{"operating_margin":0.036}', "영업이익률은 3.6%입니다. [E1]"),
    ('{"operating_margin":-0.124}', "영업이익률은 -12.4%입니다. [E1]"),
    ('{"operating_margin":-1.25}', "영업이익률은 -125%입니다. [E1]"),
    ('{"on_time_rate":0.975}', "납기 준수율은 97.5%입니다. [E1]"),
    ('{"raw_material_ratio":0.4321}', "원재료 비중은 43%입니다. [E1]"),
])
def test_ratio_fields_can_be_written_as_percent(source, answer):
    report = verify.check(answer, evidence(source))
    assert report.ok, report


@pytest.mark.parametrize(("source", "answer"), [
    ('{"debt_ratio":1.6}', "부채비율은 170%입니다. [E1]"),             # 다른 값
    ('{"total_score":1.6}', "부채비율은 160%입니다. [E1]"),            # 비율 필드가 아닌 숫자를 % 로 바꿈
    ('{"target_price":3.9}', "목표 대비 390%입니다. [E1]"),
    ('{"debt_ratio":1.6}', "부채비율은 160원입니다. [E1]"),             # 금액 단위는 금액 규칙으로 검사
    ("부채비율 1.6", "부채비율은 160%입니다. [E1]"),                    # 필드가 아닌 본문 숫자는 % 변환하지 않음
])
def test_other_numbers_are_not_converted(source, answer):
    report = verify.check(answer, evidence(source))
    assert not report.ok and report.ungrounded_numbers


class FakeBackend:
    def __init__(self, answer: str):
        self.answer = answer
        self.calls = 0

    async def generate(self, system: str, user: str) -> str:
        self.calls += 1
        return json.dumps({"answer": self.answer, "used_evidence": []}, ensure_ascii=False)


async def test_supplier_debt_ratio_answer_is_not_retried_or_masked():
    """fixture 02 실제 Routing 결과: SUP-0001 debt_ratio 1.6, SUP-0002 2.33."""
    data = json.loads((FIXTURES / "routing" / "02_parallel_suppliers.json").read_text(encoding="utf-8"))
    dec = json.loads((FIXTURES / data["decomposition"]).read_text(encoding="utf-8"))
    decomposition = DecompositionResult.model_validate(dec["expected"])
    routing = RouteResult.model_validate(data["routing"])

    backend = FakeBackend("SUP-0001의 부채비율은 160%, 납기 준수율은 97.5%입니다. [E1] "
                          "SUP-0002의 부채비율은 233%, 납기 준수율은 86.4%입니다. [E2]")
    answer = await AnswerGenerator(backend=backend).generate(decomposition, routing)

    assert backend.calls == 1                         # 재생성 없음
    assert answer.verification.ok and not answer.verification.masked
    assert "160%" in answer.text and "233%" in answer.text and verify.MASK not in answer.text
