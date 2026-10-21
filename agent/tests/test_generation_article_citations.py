"""검색 문단과 조문 전문의 인용 범위 회귀. 외부 모델이나 법령 서버를 호출하지 않는다."""

import json
from copy import deepcopy

import pytest

from negopilot_shared import DecompositionResult, RouteResult
from negopilot_agent.generation import AnswerGenerator, build_evidence
from negopilot_agent.generation import verify
from negopilot_agent.generation.answer import Evidence


ARTICLE = {
    "law_key": "fixture_act", "law_name": "가상 지급 규정", "article_no": "제13조",
    "citation": "가상 지급 규정 제13조", "source_url": "https://example.test/laws/fixture#article13",
    "paragraphs": [{"no": "①", "text": "지급 기한은 60일이다."},
                   {"no": "④", "text": "지급 지연 시 이자를 지급한다."}],
}


def route(*, article=None, error=None, server="law-mcp") -> RouteResult:
    return RouteResult.model_validate({"status": "routed", "outcomes": [{
        "id": "q1", "intent": "law_check", "status": "ok", "calls": [
            {"server": "law-mcp", "tool": "search_regulation", "args": {"query": "지급 기한"},
             "result": [{"law_key": "fixture_act", "article_no": "제13조", "paragraph": "④",
                         "citation": "가상 지급 규정 제13조④", "text": "지급 지연 시 이자를 지급한다.",
                         "source_url": ARTICLE["source_url"]}]},
            {"server": server, "tool": "get_article", "args": {"law": "fixture_act", "article_no": "제13조"},
             "result": deepcopy(ARTICLE if article is None else article), "error": error},
        ],
    }]})


def test_valid_full_article_keeps_its_own_citation_url_and_paragraph_text():
    hit, article = build_evidence(route())
    assert hit.kind == "regulation_hit" and hit.citable
    assert article.kind == "article_reference" and article.citable
    assert article.label == ARTICLE["citation"] and article.source_url == ARTICLE["source_url"]
    assert "60일" not in hit.text and "① 지급 기한은 60일이다." in article.text
    report = verify.check("지급 기한은 60일입니다. [E2]", [hit, article])
    assert report.ok
    text, citations = verify.render_citations("지급 기한은 60일입니다. [E2]", [hit, article])
    assert text.endswith("[1]")
    assert citations[0].evidence_id == article.id
    assert citations[0].label == ARTICLE["citation"]
    assert citations[0].source_url == ARTICLE["source_url"]


@pytest.mark.parametrize("updates", [
    {"citation": None}, {"citation": ""}, {"citation": "  "},
    {"source_url": None}, {"source_url": ""}, {"source_url": "  "},
    {"source_url": "javascript:alert(1)"}, {"source_url": "https:///missing-host"},
    {"source_url": "https://[invalid"}, {"paragraphs": []}, {"paragraphs": [{"text": "  "}]},
])
def test_incomplete_article_source_does_not_become_citable(updates):
    ev = build_evidence(route(article=ARTICLE | updates))
    article = next(item for item in ev if item.kind == "article_reference")
    assert not article.citable
    assert verify.check(f"참고 자료입니다. [{article.id}]", ev).unknown_citations == [article.id]


def test_only_law_mcp_article_is_promoted_to_citable():
    article = next(item for item in build_evidence(route(server="business-mcp"))
                   if item.kind == "article_reference")
    assert not article.citable


def test_failed_article_is_not_evidence_even_when_a_result_is_attached():
    ev = build_evidence(route(error="Article lookup failed."))
    assert [item.kind for item in ev] == ["regulation_hit"]
    assert "60일" not in ev[0].text
    assert verify.check("지급 기한은 60일입니다. [E2]", ev).unknown_citations == ["E2"]


def test_general_non_citable_evidence_still_cannot_be_cited():
    item = Evidence(id="E1", sub_query="q1", source="law", kind="reference", label="참고",
                    text="참고 자료", citable=False)
    report = verify.check("참고했습니다. [E1]", [item])
    assert report.unknown_citations == ["E1"]
    assert verify.render_citations("참고했습니다. [E1]", [item])[1] == []


class FullArticleBackend:
    def __init__(self):
        self.calls = []

    async def generate(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return json.dumps({"answer": "지급 기한은 60일입니다. [E2]", "used_evidence": ["E2"]},
                          ensure_ascii=False)


async def test_generator_can_support_first_paragraph_with_full_article_instead_of_fourth_hit():
    decomposition = DecompositionResult.model_validate({
        "status": "ok", "original_query": "지급 기한은?", "sub_queries": [{
            "id": "q1", "question": "지급 기한 조회", "original_text": "지급 기한은?",
            "intent": "law_check", "grounding": "required", "confidence": 1,
        }],
    })
    backend = FullArticleBackend()
    answer = await AnswerGenerator(backend=backend).generate(decomposition, route())
    assert len(backend.calls) == 1
    assert answer.verification.ok and not answer.verification.masked
    assert answer.citations[0].label == ARTICLE["citation"]
    assert answer.citations[0].label != "가상 지급 규정 제13조④"
    system, user = backend.calls[0]
    assert 'citable="yes" label="가상 지급 규정 제13조"' in user
    assert "설명과 다른 항의 검색 근거를 대신 붙이지 않는다" in system
