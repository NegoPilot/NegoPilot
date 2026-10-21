"""근거 id에 항 번호가 붙은 인용([E6①]) 처리 (#104)."""

import pytest

from negopilot_agent.generation import AnswerGenerator, verify
from negopilot_agent.generation.answer import Evidence

from tests.test_generation import FakeBackend, draft, load


def ev(eid: str, citable: bool = True) -> Evidence:
    return Evidence(id=eid, sub_query="q1", source="law", kind="article", label=f"조문 {eid}", text="본문",
                    citable=citable)


def test_paragraph_marks_are_read_as_the_evidence_id():
    text = "지급기일로 봅니다 [E6①, E6②, E4]."
    assert verify.cited_ids(text) == ["E6", "E4"]
    rendered, citations = verify.render_citations(text, [ev("E4"), ev("E6")])
    assert rendered == "지급기일로 봅니다 [1, 2]."
    assert [(c.n, c.evidence_id) for c in citations] == [(1, "E6"), (2, "E4")]


def test_unknown_id_with_paragraph_mark_is_still_caught():
    assert verify.cited_ids("근거 [E99③]") == ["E99"]


@pytest.mark.parametrize("plain", ["[E1]", "[E1, E2]", "[ E1 ,E2 ]"])
def test_plain_citations_are_unchanged(plain):
    rendered, _ = verify.render_citations(f"문장 {plain}", [ev("E1"), ev("E2")])
    assert "E" not in rendered


async def test_answer_with_paragraph_marks_is_grounded():
    """모델이 [E1①]처럼 써도 사용자에게 E id가 보이지 않고 근거 있는 답변이 된다."""
    d, r = load("01_law_article")
    gen = AnswerGenerator(backend=FakeBackend(draft("제4조는 부당한 하도급대금 결정을 금지합니다 [E1①, E1②].", ["E1"])))
    answer = await gen.generate(d, r)
    assert answer.grounded is True
    assert "E1" not in answer.text and "[1]" in answer.text
