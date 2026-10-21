"""Routing 단위 테스트.

MCP 서버 대신 가짜 ToolCaller로 shared/fixtures의 분해 결과 11개를 그대로 실행한다.
실제 서버로 돌려 보려면 business-mcp(9102), law-mcp(9103)를 띄우고 scripts/route_fixtures.py 를 쓴다.

    uv run python -m pytest -q tests/test_routing.py
"""

import asyncio
import json
from pathlib import Path

import pytest
from langgraph.graph import END, START, StateGraph

from negopilot_shared import DecompositionResult

from negopilot_agent.routing import Router, RoutingState, make_route_node
from negopilot_agent.routing.extract import select_values
from negopilot_agent.routing.results import ToolCall

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "shared" / "fixtures" / "decomposition"

RFQS = [{"rfq_id": "RFQ-2026-002", "oem_id": "OEM-A", "part_name": "도어 힌지"},
        {"rfq_id": "RFQ-2026-001", "oem_id": "OEM-B", "part_name": "브래킷"}]
RANKED = {"rfq": {"rfq_id": "RFQ-2026-001"}, "candidate_count": 3, "excluded": [],
          "ranked": [{"supplier_id": "SUP-0007", "name": "(주)대한프레스"},
                     {"supplier_id": "SUP-0003", "name": "서연이화"},
                     {"supplier_id": "SUP-0011", "name": "한일정공"}]}
SEARCH_HITS = [{"law_key": "subcontract_act", "type": "조문", "article_no": "제4조", "title": "부당한 하도급대금의 결정 금지"},
               {"law_key": "subcontract_act", "type": "조문", "article_no": "제4조", "title": "같은 조 다른 항"},
               {"law_key": "subcontract_act", "type": "부칙", "title": "부칙 <제1호>"},
               {"law_key": "subcontract_act", "type": "조문", "article_no": "제11조", "title": "감액금지"}]


class FakeCaller:
    """도구별로 정해 둔 응답을 돌려주고, 받은 호출과 동시 실행 수를 기록한다."""

    def __init__(self, overrides: dict | None = None, delay: float = 0.01):
        self.calls: list[tuple[str, str, dict]] = []
        self.overrides = overrides or {}
        self.delay = delay
        self.running = 0
        self.max_running = 0

    async def __call__(self, server, tool, args):
        self.calls.append((server, tool, args))
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            await asyncio.sleep(self.delay)
            if tool in self.overrides:
                value = self.overrides[tool]
                if isinstance(value, Exception):
                    raise value
                return value(args) if callable(value) else value
            return {
                "list_rfqs": RFQS,
                "rank_suppliers": RANKED,
                "get_supplier": {"supplier_id": args.get("supplier"), "name": "협력사"},
                "check_offer": {"verdict": "위반 가능", "findings": []},
                "search_regulation": SEARCH_HITS,
                "get_article": {"law_key": args.get("law"), "article_no": args.get("article_no"),
                                "title": "조문", "citation": f"하도급법 {args.get('article_no')}"},
            }[tool]
        finally:
            self.running -= 1

    def tools(self):
        return [t for _, t, _ in self.calls]


def fixture(name: str) -> DecompositionResult:
    data = json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return DecompositionResult.model_validate(data["expected"])


def make(sub_queries: list[dict]) -> DecompositionResult:
    base = {"question": "질문", "original_text": "원문", "grounding": "required", "confidence": 0.9,
            "entities": {}, "inputs": [], "depends_on": []}
    return DecompositionResult.model_validate({
        "status": "ok", "original_query": "테스트", "sub_queries": [base | sq for sq in sub_queries]})


@pytest.mark.parametrize("path", sorted(FIXTURE_DIR.glob("*.json")), ids=lambda p: p.stem)
async def test_every_fixture_routes(path):
    result = await Router(FakeCaller()).route(fixture(path.stem))
    expected_statuses = {"09_clarify": [], "11_failed": [], "06_general_knowledge": ["general_knowledge"],
                         "10_out_of_scope": ["out_of_scope"]}
    statuses = [o.status for o in result.outcomes]
    assert statuses == expected_statuses.get(path.stem, ["ok"] * len(statuses))


async def test_law_article_calls_get_article():
    caller = FakeCaller()
    result = await Router(caller).route(fixture("01_law_article"))
    assert caller.calls == [("law-mcp", "get_article", {"law": "subcontract_act", "article_no": "제4조"})]
    assert result.outcomes[0].calls[0].result["citation"] == "하도급법 제4조"


async def test_independent_sub_queries_run_in_parallel():
    caller = FakeCaller(delay=0.05)
    await Router(caller).route(fixture("02_parallel_suppliers"))
    assert sorted(a["supplier"] for _, _, a in caller.calls) == ["SUP-0001", "SUP-0002"]
    assert caller.max_running == 2


async def test_law_check_searches_then_fetches_articles():
    caller = FakeCaller()
    result = await Router(caller).route(fixture("03_rank_then_law"))
    law = result.outcome("q2")
    # 질문 문장으로 검색하고, 판단 근거인 price_change_pct는 도구에 넘기지 않는다
    assert law.calls[0].tool == "search_regulation"
    assert law.calls[0].args == {"query": fixture("03_rank_then_law").sub_queries[1].question,
                                 "laws": ["subcontract_act"]}
    # 조문만, 중복 없이 get_article
    assert [c.args["article_no"] for c in law.calls[1:]] == ["제4조", "제11조"]
    assert result.outcome("q1").calls[0].args == {"rfq_id": "RFQ-2026-001"}


async def test_offer_check_passes_entities_as_args():
    caller = FakeCaller()
    await Router(caller).route(fixture("05_offer_check"))
    assert caller.calls == [("business-mcp", "check_offer",
                             {"supplier": "SUP-0003", "rfq_id": "RFQ-2026-001", "unit_price": 12000,
                              "payment_days": 75, "price_linkage": False})]


async def test_inputs_top1_feeds_next_group():
    caller = FakeCaller()
    result = await Router(caller).route(fixture("08_assumption_instead_of_clarify"))
    assert caller.tools() == ["list_rfqs", "rank_suppliers"]
    assert result.outcome("q2").calls[0].args == {"rfq_id": "RFQ-2026-002"}   # 마감일 순 첫 번째


async def test_tool_less_intents_and_not_routed():
    caller = FakeCaller()
    assert (await Router(caller).route(fixture("09_clarify"))).status == "not_routed"
    assert (await Router(caller).route(fixture("11_failed"))).status == "not_routed"
    assert (await Router(caller).route(fixture("06_general_knowledge"))).outcomes[0].status == "general_knowledge"
    assert (await Router(caller).route(fixture("10_out_of_scope"))).outcomes[0].status == "out_of_scope"
    assert caller.calls == []


async def test_planned_intent_is_unsupported():
    caller = FakeCaller()
    result = await Router(caller).route(make([
        {"id": "q1", "intent": "draft_email", "entities": {"supplier": "SUP-0003"}}]))
    assert result.outcomes[0].status == "unsupported"
    assert caller.calls == []


async def test_empty_previous_result_halts():
    caller = FakeCaller({"list_rfqs": []})
    result = await Router(caller).route(fixture("08_assumption_instead_of_clarify"))
    assert [o.status for o in result.outcomes] == ["ok", "empty"]
    assert result.halted and "q1" in result.halt_reason
    assert caller.tools() == ["list_rfqs"]


async def test_groups_after_halt_are_skipped():
    caller = FakeCaller({"rank_suppliers": {"ranked": []}})
    result = await Router(caller).route(make([
        {"id": "q1", "intent": "supplier_ranking", "entities": {"rfq_id": "RFQ-2026-001"}},
        {"id": "q2", "intent": "supplier_detail", "depends_on": ["q1"],
         "inputs": [{"slot": "supplier", "from": "q1", "select": "top1"}]},
        {"id": "q3", "intent": "offer_check", "depends_on": ["q2"],
         "entities": {"rfq_id": "RFQ-2026-001", "unit_price": 1, "payment_days": 30, "price_linkage": True},
         "inputs": [{"slot": "supplier", "from": "q2", "select": "top1"}]},
    ]))
    assert [o.status for o in result.outcomes] == ["ok", "empty", "skipped"]


async def test_missing_required_args():
    caller = FakeCaller()
    result = await Router(caller).route(make([
        {"id": "q1", "intent": "offer_check",
         "entities": {"supplier": "SUP-0003", "rfq_id": "RFQ-2026-001", "unit_price": 12000}}]))
    o = result.outcomes[0]
    assert o.status == "missing_args"
    assert o.missing == ["payment_days", "price_linkage"]
    assert caller.calls == []


async def test_tool_error_skips_dependents_only():
    caller = FakeCaller({"list_rfqs": RuntimeError("DB 연결 실패")})
    result = await Router(caller).route(make([
        {"id": "q1", "intent": "rfq_lookup"},
        {"id": "q2", "intent": "law_article", "entities": {"law": "subcontract_act", "article_no": "제4조"}},
        {"id": "q3", "intent": "supplier_ranking", "depends_on": ["q1"],
         "inputs": [{"slot": "rfq_id", "from": "q1", "select": "top1"}]},
    ]))
    assert [o.status for o in result.outcomes] == ["error", "ok", "skipped"]
    assert "DB 연결 실패" in result.outcome("q1").message
    assert not result.halted


async def test_top_n_fans_out():
    caller = FakeCaller()
    result = await Router(caller).route(make([
        {"id": "q1", "intent": "supplier_ranking", "entities": {"rfq_id": "RFQ-2026-001"}},
        {"id": "q2", "intent": "supplier_detail", "depends_on": ["q1"],
         "inputs": [{"slot": "supplier", "from": "q1", "select": "top(2)"}]},
    ]))
    assert [c.args["supplier"] for c in result.outcome("q2").calls] == ["SUP-0007", "SUP-0003"]


@pytest.mark.parametrize(("select", "expected"), [
    ("top1", ["SUP-0007"]),
    ("top(2)", ["SUP-0007", "SUP-0003"]),
    ("all", ["SUP-0007", "SUP-0003", "SUP-0011"]),
    ('by_name("대한프레스")', ["SUP-0007"]),
    ('by_name("SUP-0011")', ["SUP-0011"]),
    ('by_name("없는회사")', []),
])
def test_select_values(select, expected):
    calls = [ToolCall(server="business-mcp", tool="rank_suppliers", args={}, result=RANKED)]
    assert select_values("supplier_ranking", calls, "supplier", select) == expected


async def test_route_node_in_graph():
    graph = StateGraph(RoutingState)
    graph.add_node("route", make_route_node(FakeCaller()))
    graph.add_edge(START, "route")
    graph.add_edge("route", END)
    out = await graph.compile().ainvoke({"query": "하도급법 제4조 보여줘",
                                         "decomposition": fixture("01_law_article")})
    assert out["routing"].outcomes[0].status == "ok"
