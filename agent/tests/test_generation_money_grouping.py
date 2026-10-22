"""금액 숫자의 쉼표 간격 바로잡기.

모델이 "705003098505"를 "7,050,030,985,05원"처럼 옮기면 근거 검사는 쉼표를 무시하고 통과시킨다.
값은 같지만 사용자는 7조처럼 읽는다. 쉼표 간격만 코드로 바로잡는다.

    uv run python -m pytest -q tests/test_generation_money_grouping.py
"""

import pytest

from negopilot_agent.generation import verify


@pytest.mark.parametrize("text, fixed", [
    ("매출은 7,050,030,985,05원입니다.", "매출은 705,003,098,505원입니다."),     # 실제 사례 (검증 세트 gv11)
    ("매출은 705,003,098,505원입니다.", "매출은 705,003,098,505원입니다."),        # 이미 맞으면 그대로
    ("단가 12,00원", "단가 1,200원"),
    ("부채 1,2345억원", "부채 12,345억원"),
    ("[1, 2, 3] 근거", "[1, 2, 3] 근거"),                                        # 금액이 아니면 손대지 않는다
    ("제1,2항과 3,5일", "제1,2항과 3,5일"),
])
def test_money_digit_grouping_is_fixed_without_changing_the_value(text, fixed):
    assert verify.fix_digit_grouping(text) == fixed
