"""쿼리 분해 계약 테스트.

- fixtures/decomposition/*.json(개발용)과 fixtures/validation/*.json(검증용)의 expected가 모두 스키마를 통과해야 한다
- 계약에서 금지한 형태는 스키마가 막아야 한다

    uv run python -m pytest -q
"""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from negopilot_shared import DecompositionResult, load_intents

FIXTURES = sorted((Path(__file__).resolve().parent.parent / "fixtures" / "decomposition").glob("*.json"))
VALIDATION = sorted((Path(__file__).resolve().parent.parent / "fixtures" / "validation").glob("*.json"))
# Routing fixture와 묶이지 않은 추가 개발 질문 (프롬프트 보강용)
DEV_EXTRA = sorted((Path(__file__).resolve().parent.parent / "fixtures" / "decomposition_dev").glob("*.json"))


def load(name: str) -> dict:
    path = next(p for p in FIXTURES if p.stem.startswith(name))
    return json.loads(path.read_text(encoding="utf-8"))["expected"]


@pytest.mark.parametrize("path", FIXTURES + DEV_EXTRA + VALIDATION, ids=lambda p: p.stem)
def test_fixture_matches_contract(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert {"note", "query", "expected"} <= data.keys()
    result = DecompositionResult.model_validate(data["expected"])
    assert result.original_query == data["query"]


def test_registry_entities_exist_in_entity_dictionary():
    from negopilot_shared.decomposition import ENTITY_NAMES
    for spec in load_intents().values():
        assert set(spec.entities) <= ENTITY_NAMES, spec.intent
        if spec.grounding == "none":
            assert spec.server is None and not spec.tools, spec.intent


def test_parallel_groups():
    assert DecompositionResult.model_validate(load("02")).parallel_groups() == [["q1", "q2"]]
    assert DecompositionResult.model_validate(load("03")).parallel_groups() == [["q1", "q2"]]
    assert DecompositionResult.model_validate(load("08")).parallel_groups() == [["q1"], ["q2"]]


def broken(name: str, mutate) -> dict:
    data = copy.deepcopy(load(name))
    mutate(data)
    return data


@pytest.mark.parametrize("data, message", [
    (broken("01", lambda d: d["sub_queries"][0].update(intent="weather")), "등록표에 없다"),
    (broken("06", lambda d: d["sub_queries"][0].update(grounding="required")), "grounding"),
    (broken("07", lambda d: d["sub_queries"][0].update(grounding="none")), "grounding"),
    (broken("01", lambda d: d["sub_queries"][0]["entities"].update(supplier_id="SUP-1")), "Extra inputs"),
    (broken("01", lambda d: d["sub_queries"][0]["entities"].update(rfq_id="RFQ-2026-001")), "쓸 수 없는 엔티티"),
    (broken("01", lambda d: d["sub_queries"][0]["entities"].update(article_no="4조")), "article_no"),
    (broken("08", lambda d: d["sub_queries"][1]["inputs"][0].update(select="first")), "select"),
    (broken("08", lambda d: d["sub_queries"][1].update(depends_on=[])), "depends_on에도"),
    (broken("08", lambda d: d["sub_queries"][0].update(depends_on=["q2"])), "앞에 나온"),
    (broken("02", lambda d: d["sub_queries"][1].update(id="q1")), "중복"),
    (broken("09", lambda d: d.update(clarification=None)), "clarify"),
    (broken("01", lambda d: d.update(sub_queries=[])), "1개 이상"),
    (broken("11", lambda d: d.update(sub_queries=load("01")["sub_queries"])), "failed"),
    (broken("02", lambda d: d.update(sub_queries=d["sub_queries"] * 3)), "at most 5"),
], ids=["unknown_intent", "general_with_tools", "law_without_grounding", "unknown_entity",
        "entity_not_for_intent", "bad_article_no", "unknown_select", "input_not_in_depends",
        "forward_dependency", "duplicate_id", "clarify_without_question", "ok_without_subqueries",
        "failed_with_subqueries", "too_many_subqueries"])
def test_contract_rejects(data, message):
    with pytest.raises(ValidationError, match=message):
        DecompositionResult.model_validate(data)


def test_planned_intent_is_still_valid_output():
    """mail-mcp 전이라도 분해는 draft_email을 낼 수 있다. 미지원 처리는 Routing이 한다."""
    assert load_intents()["draft_email"].status == "planned"


def test_validation_set_is_separate_from_dev_fixtures():
    """검증 세트는 개발용 fixture·등록표 예시와 질문이 겹치면 안 된다 (겹치면 채점이 의미 없다)."""
    def queries(paths):
        return {json.loads(p.read_text(encoding="utf-8"))["query"] for p in paths}
    registry_examples = {q for spec in load_intents().values() for q in spec.examples}
    assert len(VALIDATION) >= 20
    assert not queries(VALIDATION) & (queries(FIXTURES) | queries(DEV_EXTRA) | registry_examples)
