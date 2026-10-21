"""Agent 전체 그래프 테스트: decompose_query → route → generate.

비용과 서버 없이 세 단계를 한 번에 돈다.
- 분해: 가짜 백엔드가 shared/fixtures/decomposition의 expected를 그대로 돌려준다
- Routing: 가짜 MCP가 shared/fixtures/routing에 기록된 실제 서버 응답을 (도구, 인자)로 찾아 돌려준다
- 생성: 가짜 백엔드가 인용할 수 있는 첫 근거를 인용한다

    uv run python -m pytest -q tests/test_graph.py
"""

import json
from pathlib import Path

import pytest

from negopilot_shared import RouteResult

from negopilot_agent.decomposition import QueryDecomposer
from negopilot_agent.generation import AnswerGenerator
from negopilot_agent.graph import build_graph

FIXTURES = Path(__file__).resolve().parents[2] / "shared" / "fixtures"
NAMES = sorted(p.stem for p in (FIXTURES / "decomposition").glob("*.json"))

EXPECTED_KIND = {
    "01_law_article": "answer", "02_parallel_suppliers": "answer", "03_rank_then_law": "answer",
    "04_business_wording": "answer", "05_offer_check": "answer", "06_general_knowledge": "answer",
    "07_number_needs_grounding": "answer", "08_assumption_instead_of_clarify": "answer",
    "09_clarify": "clarify", "10_out_of_scope": "refusal", "11_failed": "failed",
}


def fixture(kind: str, name: str) -> dict:
    return json.loads((FIXTURES / kind / f"{name}.json").read_text(encoding="utf-8"))


def key(server: str, tool: str, args: dict) -> str:
    return json.dumps([server, tool, args], ensure_ascii=False, sort_keys=True)


class FixedDecomposition:
    """분해 백엔드 대신 정해 둔 분해 결과 JSON을 돌려준다."""

    def __init__(self, expected: dict):
        self.text = json.dumps(expected, ensure_ascii=False)

    async def generate(self, system: str, user: str) -> str:
        return self.text


class ReplayCaller:
    """routing fixture에 기록된 도구 응답을 다시 돌려준다. 기록에 없는 호출이면 실패한다."""

    def __init__(self, routing: dict):
        self.records: dict[str, object] = {}
        for o in routing["outcomes"]:
            for c in o["calls"] + o["referenced_articles"]:
                self.records[key(c["server"], c["tool"], c["args"])] = c["result"]
        self.calls: list[str] = []

    async def __call__(self, server, tool, args):
        k = key(server, tool, args)
        self.calls.append(k)
        if k not in self.records:
            raise AssertionError(f"기록에 없는 호출: {k}")
        return self.records[k]


class CiteFirst:
    async def generate(self, system: str, user: str) -> str:
        for line in user.splitlines():
            if line.startswith('<e id="') and 'citable="yes"' in line:
                eid = line.split('"')[1]
                return json.dumps({"answer": f"요청하신 내용을 정리했습니다. [{eid}]", "used_evidence": [eid]})
        return json.dumps({"answer": "RFQ는 완성차가 부품 협력사에 보내는 견적 요청서를 말합니다.", "used_evidence": []})


def make_app(name: str):
    dec = fixture("decomposition", name)
    routing = fixture("routing", name)["routing"]
    caller = ReplayCaller(routing)
    app = build_graph(decomposer=QueryDecomposer(backend=FixedDecomposition(dec["expected"])),
                      caller=caller, generator=AnswerGenerator(backend=CiteFirst()))
    return app, dec["query"], routing, caller


def test_every_fixture_is_covered():
    assert set(NAMES) == set(EXPECTED_KIND)


@pytest.mark.parametrize("name", NAMES)
async def test_question_runs_end_to_end(name):
    app, query, recorded, _ = make_app(name)
    state = await app.ainvoke({"query": query})
    assert state["answer"].kind == EXPECTED_KIND[name]
    assert state["answer"].text
    if state["decomposition"].status == "ok":
        # 그래프 안의 Routing이 fixture를 만들 때와 같은 결과를 낸다
        assert state["routing"] == RouteResult.model_validate(recorded)


@pytest.mark.parametrize("name", ["09_clarify", "11_failed"])
async def test_route_is_skipped_when_decomposition_is_not_ok(name):
    app, query, _, caller = make_app(name)
    state = await app.ainvoke({"query": query})
    assert "routing" not in state
    assert caller.calls == []


async def test_tool_answer_has_citations():
    app, query, _, _ = make_app("05_offer_check")
    answer = (await app.ainvoke({"query": query}))["answer"]
    assert answer.grounded and answer.citations
    assert answer.verification.ok
