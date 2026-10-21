"""Generation 검증 세트(eval_data/generation_validation/)와 법령 근거 재확인 문구 테스트. 모델·MCP 서버 없이 돈다.

    uv run python -m pytest -q tests/test_generation_validation_set.py
"""

import json
from pathlib import Path

import pytest

from negopilot_shared import DecompositionResult, RouteResult

from negopilot_agent.generation import build_evidence
from negopilot_agent.generation.prompt import LAW_REMINDER, SYSTEM_PROMPT, build_user_message

AGENT = Path(__file__).resolve().parents[1]
FIXTURES = AGENT.parent / "shared" / "fixtures"
SET_DIR = AGENT / "eval_data" / "generation_validation"
GOLD = json.loads((AGENT / "eval_data" / "generation_validation_gold.json").read_text(encoding="utf-8"))["items"]
DEV_GOLD = json.loads((AGENT / "eval_data" / "generation_gold.json").read_text(encoding="utf-8"))["items"]


def load(name: str) -> tuple[str, DecompositionResult, RouteResult]:
    data = json.loads((SET_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return (data["query"], DecompositionResult.model_validate(data["decomposition"]),
            RouteResult.model_validate(data["routing"]))


def evidence_of(name: str):
    _, dec, routing = load(name)
    return build_evidence(routing, {sq.id: sq.question for sq in dec.sub_queries})


def test_gold_covers_every_validation_input():
    assert {i["fixture"] for i in GOLD} == {p.stem for p in SET_DIR.glob("*.json")}
    assert len(GOLD) >= 10


@pytest.mark.parametrize("item", GOLD, ids=lambda i: i["fixture"])
def test_input_is_valid_and_routed(item):
    query, dec, routing = load(item["fixture"])
    assert dec.original_query == query
    assert routing.status == "routed"                    # route_generation_set.py로 실제 MCP 결과를 채웠다
    assert {o.id for o in routing.outcomes} == {sq.id for sq in dec.sub_queries}
    assert all(o.status == "ok" for o in routing.outcomes)


@pytest.mark.parametrize("item", GOLD, ids=lambda i: i["fixture"])
def test_gold_must_evidence_reaches_the_model(item):
    text = "\n".join(f"{e.label}\n{e.text}" for e in evidence_of(item["fixture"]))
    assert [m for m in item["must_evidence"] if m not in text] == []


def test_validation_questions_do_not_overlap_dev_set():
    """검증 질문이 개발 세트 질문과 같으면 프롬프트를 고칠 때 이미 본 질문이 된다."""
    dev = set()
    for item in DEV_GOLD:
        data = json.loads((FIXTURES / "routing" / f"{item['fixture']}.json").read_text(encoding="utf-8"))
        dev.add(data["query"].replace(" ", ""))
    validation = {load(i["fixture"])[0].replace(" ", "") for i in GOLD}
    assert dev.isdisjoint(validation)
    assert not any(q in SYSTEM_PROMPT.replace(" ", "") for q in validation)


def test_law_reminder_only_when_law_evidence():
    _, dec, routing = load("gv01_law_bill_discount")
    assert LAW_REMINDER in build_user_message(dec, build_evidence(routing), [])
    _, dec, routing = load("gv09_compare_suppliers")
    assert LAW_REMINDER not in build_user_message(dec, build_evidence(routing), [])
