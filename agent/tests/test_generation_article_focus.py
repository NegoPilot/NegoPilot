"""긴 조문에서 관련 항을 고르는 기능과 Generation 평가 정답의 근거 포함 여부 테스트.

    uv run python -m pytest -q tests/test_generation_article_focus.py
"""

import json
from pathlib import Path

import pytest

from negopilot_shared import DecompositionResult, RouteResult

from negopilot_agent.generation import build_evidence
from negopilot_agent.generation.evidence import MAX_TEXT, select_paragraphs

AGENT = Path(__file__).resolve().parents[1]
FIXTURES = AGENT.parent / "shared" / "fixtures"
GOLD = json.loads((AGENT / "eval_data" / "generation_gold.json").read_text(encoding="utf-8"))["items"]


def load(name: str) -> tuple[DecompositionResult, RouteResult]:
    data = json.loads((FIXTURES / "routing" / f"{name}.json").read_text(encoding="utf-8"))
    dec = json.loads((FIXTURES / data["decomposition"]).read_text(encoding="utf-8"))
    return DecompositionResult.model_validate(dec["expected"]), RouteResult.model_validate(data["routing"])


def evidence_of(name: str):
    dec, routing = load(name)
    return build_evidence(routing, {sq.id: sq.question for sq in dec.sub_queries})


def para(no: str, text: str) -> dict:
    return {"no": no, "text": text}


# ---------- select_paragraphs ----------

def test_short_article_is_kept_whole_in_order():
    ps = [para("①", "가나다"), para("②", "라마바")]
    kept, omitted = select_paragraphs(ps, "아무 질문", budget=100)
    assert kept == ["① 가나다", "② 라마바"] and omitted == []


def test_relevant_late_paragraph_survives_over_early_ones():
    filler = "원사업자가 수급사업자에게 제조등의 위탁을 하는 경우 서면을 발급한다. " * 6
    ps = [para(f"{i}", filler) for i in range(1, 8)]
    ps.append(para("8", '"주요 원재료"란 그 비용이 하도급대금의 100분의 10 이상인 원재료를 말한다.'))
    kept, omitted = select_paragraphs(ps, "원재료 비용이 하도급대금의 10% 이상이면 연동", budget=400)
    assert any("주요 원재료" in line for line in kept)
    assert "8" not in omitted and omitted                    # 앞쪽 항이 대신 빠진다


def test_kept_paragraphs_stay_in_article_order():
    ps = [para("①", "연동 대상 원재료 " * 20), para("②", "관계없는 내용 " * 40), para("③", "원재료 연동 산식 " * 20)]
    kept, _ = select_paragraphs(ps, "원재료 연동", budget=450)
    assert [line.split()[0] for line in kept] == ["①", "③"]


def test_single_paragraph_longer_than_budget_is_clipped_not_dropped():
    kept, omitted = select_paragraphs([para("①", "원재료 " * 500), para("②", "기타 " * 500)], "원재료", budget=200)
    assert len(kept) == 1 and kept[0].startswith("① 원재료") and kept[0].endswith("…(생략)")
    assert omitted == ["②"]


# ---------- 실제 fixture ----------

def test_offer_check_keeps_major_raw_material_definition():
    """05: check_offer가 제2조를 걸었는데, 앞에서 자르면 16번째 항(주요 원재료 정의)이 빠지던 문제."""
    article2 = next(e for e in evidence_of("05_offer_check") if e.kind == "referenced_article" and "제2조" in e.label)
    assert "100분의 10 이상인 원재료" in article2.text
    assert article2.meta["omitted_paragraphs"]                       # 길어서 일부 항은 생략
    assert "[이 질문과 관련이 적어 생략한 항:" in article2.text
    assert len(article2.text) <= MAX_TEXT + 200                      # 예산 + 머리말·생략 안내 정도


def test_law_article_shows_whole_article_when_it_fits():
    article = evidence_of("01_law_article")[0]
    assert article.kind == "article" and article.meta["omitted_paragraphs"] == []


@pytest.mark.parametrize("item", [i for i in GOLD if i["must_evidence"]], ids=lambda i: i["fixture"])
def test_gold_must_evidence_reaches_the_model(item):
    text = "\n".join(f"{e.label}\n{e.text}" for e in evidence_of(item["fixture"]))
    assert [m for m in item["must_evidence"] if m not in text] == []


def test_gold_covers_every_routing_fixture():
    assert {i["fixture"] for i in GOLD} == {p.stem for p in (FIXTURES / "routing").glob("*.json")}
