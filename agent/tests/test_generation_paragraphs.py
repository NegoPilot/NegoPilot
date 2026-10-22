"""근거 검사가 조문의 항 번호까지 확인하는지 (#160). 모델은 부르지 않는다.

조 번호만 보면 "제13조 제18항"처럼 있는 조에 없는 항을 붙여도 통과한다.
실제 사례: 연동 정의(제2조⑱)를 제13조에 붙인 답변 (PR #155 확인 중 LangSmith 추적으로 발견).

    uv run python -m pytest -q tests/test_generation_paragraphs.py
"""

import pytest

from negopilot_agent.generation import verify
from negopilot_agent.generation.answer import Evidence

ACT = "「하도급거래 공정화에 관한 법률」"


def article(eid: str, no: str, kept: list[str], omitted: list[str] | None = None) -> Evidence:
    """get_article 근거. 본문에는 고른 항만 있고, 생략한 항은 meta에 번호만 남는다 (evidence.from_get_article)."""
    lines = "\n".join(f"{p} {no}의 {p}항 내용" for p in kept)
    return Evidence(id=eid, sub_query="q1", source="law", kind="referenced_article",
                    label=f"{ACT} {no}(조문) [시행 2026-10-02]", text=f"{ACT} {no}(조문)\n{lines}",
                    meta={"law_key": "subcontract_act", "article_no": no, "omitted_paragraphs": omitted or []})


def hit(eid: str, no: str, paragraph: str) -> Evidence:
    """search_regulation 근거. 항 하나만 보인다."""
    return Evidence(id=eid, sub_query="q1", source="law", kind="regulation_hit",
                    label=f"{ACT} {no}{paragraph}(조문) [시행 2026-10-02]", text=f"{ACT} {no}{paragraph}\n내용",
                    meta={"law_key": "subcontract_act", "article_no": no, "paragraph": paragraph})


# 실제 사례와 같은 근거: 제13조는 ①~⑪, 제2조는 ⑯·⑰·⑱이 보이고 나머지는 생략
EVIDENCE = [
    article("E1", "제13조", ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩", "⑪"]),
    article("E2", "제2조", ["⑯", "⑰", "⑱"], omitted=["①", "②", "③", "④", "⑤"]),
]


@pytest.mark.parametrize("answer", [
    "연동은 하도급법 제13조 제18항에 따른 조정입니다. [E1]",      # 실제 사례
    "연동은 하도급법 제13조제18항에 따른 조정입니다. [E1]",
    "연동은 하도급법 제13조 18항에 따른 조정입니다. [E1]",
    "연동은 하도급법 제13조⑱에 따른 조정입니다. [E1]",
])
def test_paragraph_that_the_article_does_not_have_is_ungrounded(answer):
    report = verify.check(answer, EVIDENCE)
    assert not report.ok
    assert len(report.ungrounded_articles) == 1
    assert "18" in report.ungrounded_articles[0] or "⑱" in report.ungrounded_articles[0]


@pytest.mark.parametrize("answer", [
    "지급 기한은 하도급법 제13조 제1항에 있습니다. [E1]",
    "지급 기한은 하도급법 제13조①에 있습니다. [E1]",
    "연동 정의는 하도급법 제2조 제18항입니다. [E2]",
    "연동 정의는 제2조⑱입니다. [E2]",
    "정의 규정은 제2조 제3항에도 있습니다. [E2]",          # 생략한 항도 그 조에 실제로 있는 항이다
    "지급 기한은 제13조에 있습니다. [E1]",                 # 항 없이 조만 쓴 경우는 기존 검사 그대로
])
def test_paragraphs_present_in_evidence_pass(answer):
    report = verify.check(answer, EVIDENCE)
    assert report.ok, report


def test_paragraph_from_a_search_hit_counts_as_evidence():
    evidence = [hit("E1", "제13조", "①")]
    assert verify.check("지급 기한은 하도급법 제13조 제1항에 있습니다. [E1]", evidence).ok
    report = verify.check("기성금 지급은 하도급법 제13조 제3항에 있습니다. [E1]", evidence)
    assert report.ungrounded_articles == ["제13조 제3항"]             # 검색에서 본 적 없는 항


def test_article_without_paragraph_information_is_not_judged_by_paragraph():
    """항 정보가 전혀 없는 조문(예: 항이 하나뿐인 조)은 항 번호로 판단하지 않는다. 오탐을 막는다."""
    evidence = [Evidence(id="E1", sub_query="q1", source="law", kind="article",
                         label=f"{ACT} 제3조의2(조문)", text=f"{ACT} 제3조의2(조문)\n항 표시 없는 본문",
                         meta={"article_no": "제3조의2"})]
    assert verify.check("제3조의2 제1항에 따릅니다. [E1]", evidence).ok


def test_paragraph_in_the_user_question_is_allowed():
    evidence = [article("E1", "제13조", ["①"])]
    assert verify.check("질문하신 제13조 제5항은 확인되지 않았습니다. [E1]", evidence,
                        allowed_texts=["제13조 제5항이 뭐야?"]).ok


def test_ungrounded_paragraph_is_masked_whole_and_explained_in_feedback():
    answer = "연동은 하도급법 제13조 제18항에 따른 조정이고, 지급 기한은 제13조 제1항입니다. [E1]"
    report = verify.check(answer, EVIDENCE)
    masked = verify.mask(answer, report)
    assert "제13조 제18항" not in masked and verify.MASK in masked
    assert "제13조 제1항" in masked                       # 근거 있는 같은 조의 다른 항은 그대로
    assert "제13조 제18항" in verify.feedback(report)
