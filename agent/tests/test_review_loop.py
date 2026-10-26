"""검토 루프 테스트: generate → review → research (→ generate).

모델과 MCP 서버 없이 가짜 분해·도구·생성으로 두 가지 보강을 확인한다.
- suggest: 협력사 검색 0건 → 인접 지역·조건을 뺀 검색을 미리 해 보고, 결과가 있는 검색만 버튼(answer.options)으로 권함.
  답과 분해 조건은 바꾸지 않는다 (사용자가 고른 조건을 Agent가 마음대로 바꾸지 않음)
- retry_error: 일시 오류로 실패한 도구 호출을 다시 실행 (공개 오류 코드가 붙은 입력 오류는 제외)

    uv run python -m pytest -q tests/test_review_loop.py
"""

import json
import re

import pytest

from negopilot_shared import DecompositionResult, RouteResult

from negopilot_agent.decomposition import QueryDecomposer
from negopilot_agent.generation import Answer, AnswerGenerator
from negopilot_agent.graph import build_graph
from negopilot_agent.review import ReviewLog, Suggestion, plan, with_suggestions
from negopilot_agent.review.reviewer import _region_abbr
from negopilot_agent.routing import Router

from negopilot_agent.api import generation_stubs
from negopilot_agent.api.app import create_app
from negopilot_agent.api.settings import ApiSettings

from tests.test_generation_http_contract import (API_KEY, assert_wire, no_external_dependencies,  # noqa: F401
                                                 payload, post)

ROWS = [{"supplier_id": "SUP-0060", "name": "가상 프레스", "region": "경남", "process": "프레스"},
        {"supplier_id": "SUP-0052", "name": "가상 금형", "region": "울산", "process": "프레스"}]
SUPPLIER = {"supplier_id": "SUP-0060", "name": "가상 프레스", "region": "경남", "payment_days": 60}


def sub(qid="q1", intent="supplier_search", entities=None, **extra):
    return {"id": qid, "question": extra.pop("question", "프레스 협력사 검색"), "original_text": extra.pop("original_text", "제주 프레스 협력사"),
            "intent": intent, "grounding": "required", "confidence": 0.9, "entities": entities or {}} | extra


def decomposition(*subs, query="제주 프레스 협력사 찾아줘") -> dict:
    return {"schema_version": "0.1", "status": "ok", "original_query": query, "sub_queries": list(subs),
            "clarification": None, "assumptions": []}


class Fixed:
    def __init__(self, expected: dict):
        self.text = json.dumps(expected, ensure_ascii=False)

    async def generate(self, system: str, user: str) -> str:
        return self.text


class Caller:
    """도구별 응답 함수를 받아 호출을 기록한다."""

    def __init__(self, **tools):
        self.tools = tools
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, server, tool, args):
        self.calls.append((tool, dict(args)))
        return self.tools[tool](args, len([c for c in self.calls if c[0] == tool]))


class CiteAll:
    """인용할 수 있는 근거를 모두 인용한다. 근거가 없으면 모델 호출 없이 끝나는 경로라 불리지 않는다."""

    def __init__(self):
        self.prompts: list[str] = []

    async def generate(self, system: str, user: str) -> str:
        self.prompts.append(user)
        ids = re.findall(r'<e id="(E\d+)"[^>]*citable="yes"', user)
        return json.dumps({"answer": f"확인한 협력사입니다. [{', '.join(ids)}]", "used_evidence": ids})


def app_for(dec: dict, caller: Caller, generator_backend, rounds: int | None = 1):
    return build_graph(decomposer=QueryDecomposer(backend=Fixed(dec)), caller=caller,
                       generator=AnswerGenerator(backend=generator_backend), review_rounds=rounds)


# ---------- suggest (0건 → 넓힌 검색을 버튼으로 권함) ----------

def search_by_region(args, _n):
    return [] if "region" in args else ROWS


def only_in(*regions):
    def search(args, _n):
        return ROWS[:1] if args.get("region") in regions else []
    return search


async def test_empty_region_suggests_adjacent_regions_that_have_results():
    caller = Caller(search_suppliers=only_in("부산"))
    backend = CiteAll()
    dec = decomposition(sub(entities={"process": "프레스", "region": "제주"}))
    state = await app_for(dec, caller, backend).ainvoke({"query": dec["original_query"]})

    # 제주와 뱃길이 가까운 전남·부산을 미리 찾아 보고, 결과가 있는 부산만 권한다
    assert [args.get("region") for _, args in caller.calls] == ["제주", "전남", "부산"]
    answer = state["answer"]
    assert answer.kind == "no_result" and not answer.citations
    assert answer.options == ["부산 프레스 협력사 찾아줘"]
    assert answer.text.endswith("제주에는 없지만 인접 지역(부산)에는 같은 조건의 협력사가 있습니다. "
                                "아래 버튼을 누르면 그 조건으로 다시 찾아볼 수 있습니다.")
    assert answer.text.count("없습니다") == 1   # 결과 없음 안내를 되풀이하지 않는다
    # 답과 분해·Routing은 사용자가 고른 조건(제주) 그대로다. 모델도 부르지 않는다
    assert state["decomposition"].sub_queries[0].entities.region == "제주"
    assert state["decomposition"].assumptions == []
    assert state["routing"].outcomes[0].status == "empty"
    assert backend.prompts == []
    action = state["review"].rounds[0].actions[0]
    assert (action.kind, action.resolved, action.detail["nearby"]) == ("suggest", True, ["부산"])
    assert not state["review"].rounds[0].regenerated


async def test_every_adjacent_region_with_results_becomes_a_button():
    caller = Caller(search_suppliers=only_in("광주", "경남"))
    dec = decomposition(sub(entities={"process": "단조", "region": "전남", "iatf_required": True}))
    state = await app_for(dec, caller, CiteAll()).ainvoke({"query": dec["original_query"]})

    assert state["answer"].options == ["광주 IATF 인증 단조 협력사 찾아줘", "경남 IATF 인증 단조 협력사 찾아줘"]
    assert "인접 지역(광주, 경남)" in state["answer"].text


@pytest.mark.parametrize("name,abbr", [("제주특별자치도", "제주"), ("경상남도", "경남"), ("부산광역시", "부산"),
                                       ("충청북도", "충북"), ("경기도", "경기"), ("서울", "서울")])
def test_region_names_map_to_business_abbreviations(name, abbr):
    assert _region_abbr(name) == abbr


async def test_without_adjacent_results_the_region_condition_is_dropped():
    caller = Caller(search_suppliers=search_by_region)
    dec = decomposition(sub(entities={"process": "프레스", "region": "제주"}))
    state = await app_for(dec, caller, CiteAll()).ainvoke({"query": dec["original_query"]})

    # 인접 지역(전남, 부산)에도 없으면 지역 조건을 뺀 검색을 권한다
    assert [args for _, args in caller.calls] == [
        {"process": "프레스", "region": "제주"}, {"process": "프레스", "region": "전남"},
        {"process": "프레스", "region": "부산"}, {"process": "프레스"}]
    answer = state["answer"]
    assert answer.options == ["프레스 협력사 찾아줘"]
    assert "지역(제주) 조건을 빼면 찾을 수 있는 협력사가 있습니다. 아래 버튼을 누르면" in answer.text
    assert state["review"].rounds[0].actions[0].detail["dropped"] == ["region"]


async def test_conditions_are_dropped_one_by_one_until_results_appear():
    def needs_no_capacity(args, _n):
        return [] if {"region", "min_monthly_capacity"} & set(args) else ROWS

    caller = Caller(search_suppliers=needs_no_capacity)
    dec = decomposition(sub(entities={"process": "사출", "min_monthly_capacity": 500000}))
    state = await app_for(dec, caller, CiteAll()).ainvoke({"query": dec["original_query"]})

    assert [args for _, args in caller.calls] == [{"process": "사출", "min_monthly_capacity": 500000}, {"process": "사출"}]
    assert state["answer"].options == ["사출 협력사 찾아줘"]
    assert "최소 월 생산능력(500,000개 이상) 조건을 빼면 찾을 수 있는 협력사가 있습니다." in state["answer"].text


async def test_search_sentence_keeps_the_conditions_that_were_not_dropped():
    def needs_no_region(args, _n):
        return [] if "region" in args else ROWS

    caller = Caller(search_suppliers=needs_no_region)
    dec = decomposition(sub(entities={"process": "프레스", "material": "SPCC", "region": "세종",
                                      "min_monthly_capacity": 50000}))
    state = await app_for(dec, caller, CiteAll()).ainvoke({"query": dec["original_query"]})
    assert state["answer"].options == ["월 생산능력 50,000개 이상 SPCC 프레스 협력사 찾아줘"]


async def test_nothing_to_suggest_keeps_the_no_result_answer():
    caller = Caller(search_suppliers=lambda args, n: [])
    backend = CiteAll()
    dec = decomposition(sub(entities={"process": "프레스", "region": "제주"}))
    state = await app_for(dec, caller, backend).ainvoke({"query": dec["original_query"]})

    assert state["answer"].kind == "no_result" and state["answer"].options == []
    assert state["review"].rounds[0].actions[0].resolved is False
    assert backend.prompts == []


async def test_process_and_material_are_never_dropped():
    caller = Caller(search_suppliers=lambda args, n: [])
    dec = decomposition(sub(entities={"process": "프레스", "material": "SPCC"}))
    state = await app_for(dec, caller, CiteAll()).ainvoke({"query": dec["original_query"]})

    assert len(caller.calls) == 1
    assert state["review"].rounds == [] and state["answer"].kind == "no_result"


async def test_partial_answer_keeps_its_text_and_gets_the_buttons():
    """다른 하위 질문은 답이 나오고 검색만 0건이면, 그 답 뒤에 안내와 버튼을 붙인다."""
    caller = Caller(get_supplier=lambda args, n: SUPPLIER, search_suppliers=only_in("부산"))
    backend = CiteAll()
    dec = decomposition(sub(intent="supplier_detail", entities={"supplier": "SUP-0060"}),
                        sub("q2", entities={"process": "프레스", "region": "제주"}),
                        query="SUP-0060 알려주고 제주 프레스 협력사도 찾아줘")
    state = await app_for(dec, caller, backend).ainvoke({"query": dec["original_query"]})

    answer = state["answer"]
    assert answer.kind == "answer" and answer.citations
    assert answer.text.startswith("확인한 협력사입니다.")
    assert answer.options == ["부산 프레스 협력사 찾아줘"]
    assert len(backend.prompts) == 1


async def test_retry_and_suggestion_in_the_same_round():
    """오류 재시도로 답을 다시 쓰더라도, 다시 쓴 답에 버튼이 붙는다."""
    def flaky(args, n):
        if n == 1:
            raise ConnectionError("일시 오류")
        return SUPPLIER

    caller = Caller(get_supplier=flaky, search_suppliers=only_in("부산"))
    backend = CiteAll()
    dec = decomposition(sub(intent="supplier_detail", entities={"supplier": "SUP-0060"}),
                        sub("q2", entities={"process": "프레스", "region": "제주"}),
                        query="SUP-0060 알려주고 제주 프레스 협력사도 찾아줘")
    state = await app_for(dec, caller, backend).ainvoke({"query": dec["original_query"]})

    assert [a.kind for a in state["review"].rounds[0].actions] == ["retry_error", "suggest"]
    assert state["review"].rounds[0].regenerated and len(state["review"].rounds) == 1
    assert state["answer"].kind == "answer" and state["answer"].options == ["부산 프레스 협력사 찾아줘"]


def test_existing_options_are_not_replaced():
    choose = Answer(kind="answer", text="어느 회사인가요?", options=["SUP-0001 한빛", "SUP-0002 한빛정밀"])
    suggestion = Suggestion(note="인접 지역에는 있어요.", options=["부산 협력사 찾아줘"])
    assert with_suggestions(choose, [suggestion]) == choose
    clarify = Answer(kind="clarify", text="어느 지역인가요?")
    assert with_suggestions(clarify, [suggestion]) == clarify


# ---------- retry_error ----------

async def test_transient_tool_error_is_retried_once():
    def flaky(args, n):
        if n == 1:
            raise ConnectionError("일시 오류")
        return SUPPLIER

    caller = Caller(get_supplier=flaky)
    backend = CiteAll()
    dec = decomposition(sub(intent="supplier_detail", entities={"supplier": "SUP-0060"}), query="SUP-0060 알려줘")
    state = await app_for(dec, caller, backend).ainvoke({"query": dec["original_query"]})

    assert [t for t, _ in caller.calls] == ["get_supplier", "get_supplier"]
    assert state["routing"].outcomes[0].status == "ok"
    assert state["answer"].kind == "answer" and state["answer"].citations
    action = state["review"].rounds[0].actions[0]
    assert (action.kind, action.resolved) == ("retry_error", True)


async def test_error_that_persists_is_not_regenerated():
    def broken(args, n):
        raise ConnectionError("계속 실패")

    caller = Caller(get_supplier=broken)
    dec = decomposition(sub(intent="supplier_detail", entities={"supplier": "SUP-0060"}), query="SUP-0060 알려줘")
    state = await app_for(dec, caller, CiteAll()).ainvoke({"query": dec["original_query"]})

    assert len(caller.calls) == 2
    assert state["answer"].kind == "no_result"
    assert state["review"].rounds[0].regenerated is False


async def test_public_input_error_is_not_retried():
    def missing(args, n):
        raise ValueError("[RFQ_NOT_FOUND] RFQ-2026-999 없음")

    caller = Caller(rank_suppliers=missing)
    dec = decomposition(sub(intent="supplier_ranking", entities={"rfq_id": "RFQ-2026-999"}), query="RFQ-2026-999 추천")
    state = await app_for(dec, caller, CiteAll()).ainvoke({"query": dec["original_query"]})

    assert len(caller.calls) == 1
    assert state["review"].rounds == []


async def test_retry_also_runs_dependents_that_were_skipped():
    rank = {"rfq_id": "RFQ-2026-001", "ranked": [{"supplier_id": "SUP-0060", "name": "가상 프레스", "flags": []}]}

    def flaky_rank(args, n):
        if n == 1:
            raise ConnectionError("일시 오류")
        return rank

    caller = Caller(rank_suppliers=flaky_rank, get_supplier=lambda args, n: SUPPLIER)
    dec = decomposition(
        sub(intent="supplier_ranking", entities={"rfq_id": "RFQ-2026-001"}),
        sub("q2", intent="supplier_detail", inputs=[{"slot": "supplier", "from": "q1", "select": "top1"}],
            depends_on=["q1"]),
        query="RFQ-2026-001 1위 협력사 상세")
    state = await app_for(dec, caller, CiteAll()).ainvoke({"query": dec["original_query"]})

    assert [t for t, _ in caller.calls] == ["rank_suppliers", "rank_suppliers", "get_supplier"]
    assert [o.status for o in state["routing"].outcomes] == ["ok", "ok"]
    assert state["answer"].kind == "answer"


# ---------- 루프 제어 ----------

async def test_zero_rounds_keeps_the_old_single_pass_graph():
    caller = Caller(search_suppliers=search_by_region)
    dec = decomposition(sub(entities={"process": "프레스", "region": "제주"}))
    state = await app_for(dec, caller, CiteAll(), rounds=0).ainvoke({"query": dec["original_query"]})

    assert len(caller.calls) == 1
    assert state["answer"].kind == "no_result"
    assert state["review"].rounds == [] and state["review"].pending == []


async def test_rounds_follow_the_environment(monkeypatch):
    monkeypatch.setenv("NEGOPILOT_REVIEW_ROUNDS", "0")
    caller = Caller(search_suppliers=search_by_region)
    dec = decomposition(sub(entities={"process": "프레스", "region": "제주"}))
    state = await app_for(dec, caller, CiteAll(), rounds=None).ainvoke({"query": dec["original_query"]})
    assert len(caller.calls) == 1 and state["review"].rounds == []


def test_nothing_to_do_without_routing():
    dec = DecompositionResult.model_validate(decomposition(sub()))
    assert plan(dec, None, None) == []
    assert plan(dec, RouteResult(status="not_routed"), None) == []


async def test_rerun_leaves_other_outcomes_untouched():
    caller = Caller(get_supplier=lambda args, n: SUPPLIER)
    dec = DecompositionResult.model_validate(decomposition(
        sub(intent="supplier_detail", entities={"supplier": "SUP-0060"}),
        sub("q2", intent="supplier_detail", entities={"supplier": "SUP-0052"})))
    before = RouteResult.model_validate({"status": "routed", "outcomes": [
        {"id": "q1", "intent": "supplier_detail", "status": "error",
         "calls": [{"server": "business-mcp", "tool": "get_supplier", "args": {}, "error": "x"}], "message": "x"},
        {"id": "q2", "intent": "supplier_detail", "status": "ok",
         "calls": [{"server": "business-mcp", "tool": "get_supplier", "args": {"supplier": "SUP-0052"}, "result": {}}]}]})
    after = await Router(caller).rerun(dec, before, {"q1"})
    assert caller.calls == [("get_supplier", {"supplier": "SUP-0060"})]
    assert after.outcomes[0].status == "ok" and after.outcomes[1] == before.outcomes[1]


# ---------- HTTP (stub 모드, 실제 API 경로) ----------

@pytest.mark.usefixtures("no_external_dependencies")
async def test_http_empty_region_search_offers_adjacent_region_button(monkeypatch):
    calls = []
    original = generation_stubs.GenerationStubToolCaller.__call__

    async def observe(caller, server, tool, args):
        calls.append((tool, dict(args)))
        return await original(caller, server, tool, args)

    monkeypatch.setattr(generation_stubs.GenerationStubToolCaller, "__call__", observe)
    app = create_app(settings=ApiSettings(mode="stub", internal_api_key=API_KEY, request_timeout_seconds=2))
    data = assert_wire(await post(app, payload("제주 협력사 찾아줘")))

    assert data["execution_status"] == "COMPLETED"
    assert data["answer"]["kind"] == "no_result"
    # 버튼 문장은 스텁 분해가 아는 질문이라, 누르면 부산 검색으로 이어진다
    assert data["answer"]["options"] == ["부산 협력사 찾아줘"]
    assert "인접 지역(부산)" in data["answer"]["text"]
    assert calls == [("search_suppliers", {"region": "제주"}), ("search_suppliers", {"region": "전남"}),
                     ("search_suppliers", {"region": "부산"})]
    assert data["routing"]["outcomes"][0]["status"] == "empty"
    assert data["decomposition"]["assumptions"] == []

    follow = assert_wire(await post(app, payload("부산 협력사 찾아줘")))
    assert follow["answer"]["kind"] == "answer" and follow["answer"]["citations"]
