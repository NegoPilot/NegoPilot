"""검토 루프 테스트: generate → review → research → generate.

모델과 MCP 서버 없이 가짜 분해·도구·생성·검토자로 세 가지 보강을 확인한다.
- relax: 협력사 검색 0건 → 인접 지역에서 찾고, 없으면 지역 등 조건을 빼고 다시 검색. 바꾼 조건은 "가정:"으로 밝힘
- retry_error: 일시 오류로 실패한 도구 호출을 다시 실행 (공개 오류 코드가 붙은 입력 오류는 제외)
- follow_up: 모델 검토자가 답에서 빠진 부분을 보충 질문으로 내면, 분해해 하위 질문을 붙이고 실행한 뒤 다시 생성

    uv run python -m pytest -q tests/test_review_loop.py
"""

import json
import re

import pytest

from negopilot_shared import DecompositionResult, RouteResult

from negopilot_agent.decomposition import QueryDecomposer
from negopilot_agent.generation import AnswerGenerator
from negopilot_agent.graph import build_graph
from negopilot_agent.review import Critic, Critique, ReviewLog, follow_ups, plan
from negopilot_agent.review.critic import build_user_message
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


class Sequence:
    """분해 백엔드: 부를 때마다 다음 분해 결과를 돌려준다 (원래 질문, 보충 질문 순)."""

    def __init__(self, *results: dict):
        self.texts = [json.dumps(r, ensure_ascii=False) for r in results]
        self.users: list[str] = []

    async def generate(self, system: str, user: str) -> str:
        self.users.append(user)
        return self.texts[min(len(self.users), len(self.texts)) - 1]


class CriticSays:
    """검토자 백엔드: 정해 둔 판단을 돌려주고 받은 입력을 기록한다."""

    def __init__(self, *missing: tuple[str, str], text: str | None = None):
        self.text = text if text is not None else json.dumps(
            {"complete": not missing, "missing": [{"aspect": a, "question": q} for a, q in missing]},
            ensure_ascii=False)
        self.users: list[str] = []

    async def generate(self, system: str, user: str) -> str | None:
        self.users.append(user)
        return self.text


def app_for(dec: dict, caller: Caller, generator_backend, rounds: int | None = 1, critic=False, follow=None):
    backend = Sequence(dec, follow) if follow else Fixed(dec)
    return build_graph(decomposer=QueryDecomposer(backend=backend), caller=caller,
                       generator=AnswerGenerator(backend=generator_backend), review_rounds=rounds,
                       critic=Critic(backend=critic) if critic else False)


# ---------- relax ----------

def search_by_region(args, _n):
    return [] if "region" in args else ROWS


async def test_empty_region_is_widened_to_adjacent_regions_first():
    def busan_only(args, _n):
        return ROWS[:1] if args.get("region") == "부산" else []

    caller = Caller(search_suppliers=busan_only)
    backend = CiteAll()
    dec = decomposition(sub(entities={"process": "프레스", "region": "제주"}))
    state = await app_for(dec, caller, backend).ainvoke({"query": dec["original_query"]})

    # 제주와 뱃길이 가까운 전남·부산을 찾아본다. 조건을 빼지는 않는다
    assert [args.get("region") for _, args in caller.calls] == ["제주", "전남", "부산"]
    answer = state["answer"]
    assert answer.kind == "answer" and answer.citations
    assert any("제주에 없어 인접 지역(부산)에서 찾았습니다" in n for n in answer.notices)
    # 분해에는 사용자가 말한 지역이 그대로 남고, 결과에는 부산 검색이 들어간다
    assert state["decomposition"].sub_queries[0].entities.region == "제주"
    assert [c.args["region"] for c in state["routing"].outcomes[0].calls] == ["부산"]
    action = state["review"].rounds[0].actions[0]
    assert (action.kind, action.resolved, action.detail["nearby"]) == ("relax", True, ["부산"])


@pytest.mark.parametrize("name,abbr", [("제주특별자치도", "제주"), ("경상남도", "경남"), ("부산광역시", "부산"),
                                       ("충청북도", "충북"), ("경기도", "경기"), ("서울", "서울")])
def test_region_names_map_to_business_abbreviations(name, abbr):
    assert _region_abbr(name) == abbr


async def test_empty_search_is_relaxed_and_the_dropped_condition_is_disclosed():
    caller = Caller(search_suppliers=search_by_region)
    backend = CiteAll()
    dec = decomposition(sub(entities={"process": "프레스", "region": "제주"}))
    state = await app_for(dec, caller, backend).ainvoke({"query": dec["original_query"]})

    # 인접 지역(전남, 부산)에도 없으면 지역 조건을 뺀다
    assert [args for _, args in caller.calls] == [
        {"process": "프레스", "region": "제주"}, {"process": "프레스", "region": "전남"},
        {"process": "프레스", "region": "부산"}, {"process": "프레스"}]
    answer = state["answer"]
    assert answer.kind == "answer" and answer.citations and answer.verification.ok
    assert any("지역(제주) 조건을 빼고 다시 찾았습니다" in n for n in answer.notices)
    assert "지역(제주)" in answer.text   # 답 끝 안내로 사용자에게 보인다
    # 실제로 실행한 조건이 분해 결과에 남는다
    assert state["decomposition"].sub_queries[0].entities.region is None
    review: ReviewLog = state["review"]
    assert len(review.rounds) == 1 and review.rounds[0].regenerated
    action = review.rounds[0].actions[0]
    assert (action.kind, action.resolved, action.detail["dropped"]) == ("relax", True, ["region"])
    assert len(backend.prompts) == 1   # 0건일 때는 모델을 부르지 않고, 보강 뒤 한 번만 쓴다


async def test_conditions_are_dropped_one_by_one_until_results_appear():
    def needs_no_capacity(args, _n):
        return [] if {"region", "min_monthly_capacity"} & set(args) else ROWS

    caller = Caller(search_suppliers=needs_no_capacity)
    dec = decomposition(sub(entities={"process": "프레스", "region": "제주", "min_monthly_capacity": 50000}))
    state = await app_for(dec, caller, CiteAll()).ainvoke({"query": dec["original_query"]})

    assert [args for _, args in caller.calls] == [
        {"process": "프레스", "region": "제주", "min_monthly_capacity": 50000},
        {"process": "프레스", "region": "전남", "min_monthly_capacity": 50000},
        {"process": "프레스", "region": "부산", "min_monthly_capacity": 50000},
        {"process": "프레스", "min_monthly_capacity": 50000},
        {"process": "프레스"},
    ]
    assert state["review"].rounds[0].actions[0].detail["dropped"] == ["region", "min_monthly_capacity"]
    assert any("지역(제주), 최소 월 생산능력(50,000개 이상)" in n for n in state["answer"].notices)


async def test_relax_that_still_finds_nothing_keeps_the_no_result_answer():
    caller = Caller(search_suppliers=lambda args, n: [])
    backend = CiteAll()
    dec = decomposition(sub(entities={"process": "프레스", "region": "제주"}))
    state = await app_for(dec, caller, backend).ainvoke({"query": dec["original_query"]})

    assert state["answer"].kind == "no_result"
    assert state["review"].rounds[0].actions[0].resolved is False
    assert not state["review"].rounds[0].regenerated
    assert state["decomposition"].assumptions == []
    assert backend.prompts == []


async def test_process_and_material_are_never_dropped():
    caller = Caller(search_suppliers=lambda args, n: [])
    dec = decomposition(sub(entities={"process": "프레스", "material": "SPCC"}))
    state = await app_for(dec, caller, CiteAll()).ainvoke({"query": dec["original_query"]})

    assert len(caller.calls) == 1
    assert state["review"].rounds == [] and state["answer"].kind == "no_result"


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


# ---------- follow_up (모델 검토자) ----------

LAW_HITS = [{"law_key": "subcontract_act", "article_no": "제16조의2", "type": "조문",
             "citation": "하도급법 제16조의2①", "title": "공급원가 변동에 따른 하도급대금의 조정",
             "text": "원사업자는 공급원가가 변동되는 경우 하도급대금을 조정하여야 한다."}]
ARTICLE_16_2 = {
    "law_key": "subcontract_act", "law_name": "하도급거래 공정화에 관한 법률", "article_no": "제16조의2",
    "title": "공급원가 변동에 따른 하도급대금의 조정", "chapter": None, "deleted": False,
    "paragraphs": [{"no": "①", "text": "원사업자는 공급원가가 변동되는 경우 하도급대금을 조정하여야 한다."}],
    "citation": "「하도급거래 공정화에 관한 법률」 제16조의2", "effective_date": "2026-10-02", "as_of": "2026-10-23",
    "source_url": "https://www.law.go.kr/법령/하도급거래공정화에관한법률", "upcoming_changes": [],
}
LAW_TOOLS = {"search_regulation": lambda args, n: LAW_HITS, "get_article": lambda args, n: ARTICLE_16_2}


def price_question() -> dict:
    return decomposition(sub(intent="supplier_detail", entities={"supplier": "SUP-0060"},
                             question="SUP-0060 정보", original_text="SUP-0060"),
                         query="SUP-0060이 원자재 값이 올라 단가를 8% 올려 달래. 받아 줘야 해?")


def law_follow_up() -> dict:
    return decomposition(sub(intent="law_check", entities={},
                             question="원자재 가격 변동에 따른 하도급대금 조정 요건", original_text="연동 약정"),
                         query="원자재 가격이 오를 때 하도급대금 조정 요건은?")


async def test_critic_follow_up_adds_a_sub_query_and_rewrites_the_answer():
    caller = Caller(get_supplier=lambda args, n: SUPPLIER, **LAW_TOOLS)
    backend = CiteAll()
    critic = CriticSays(("단가 인상 요청인데 하도급대금 조정 규정을 다루지 않음",
                         "원자재 가격이 오를 때 하도급대금 조정 요건은?"))
    dec = price_question()
    state = await app_for(dec, caller, backend, critic=critic, follow=law_follow_up()).ainvoke(
        {"query": dec["original_query"]})

    subs = state["decomposition"].sub_queries
    assert [(s.id, s.intent) for s in subs] == [("q1", "supplier_detail"), ("q2", "law_check")]
    assert state["decomposition"].original_query == dec["original_query"]   # 원래 질문은 그대로
    # 기존 결과(get_supplier)는 다시 부르지 않고 새 하위 질문의 법령 도구만 부른다
    assert [t for t, _ in caller.calls] == ["get_supplier", "search_regulation", "get_article"]
    assert [o.status for o in state["routing"].outcomes] == ["ok", "ok"]
    assert len(backend.prompts) == 2 and "제16조의2" in backend.prompts[1]
    action = state["review"].rounds[0].actions[0]
    assert (action.kind, action.resolved, action.detail["added"]) == ("follow_up", True, ["q2"])
    assert state["review"].rounds[0].regenerated
    # 검토자는 질문·하위 질문·근거·답을 본다
    assert "<question>SUP-0060이 원자재 값이" in critic.users[0] and "supplier_detail" in critic.users[0]


async def test_complete_answer_is_left_alone():
    caller = Caller(get_supplier=lambda args, n: SUPPLIER)
    backend = CiteAll()
    critic = CriticSays()
    dec = price_question()
    state = await app_for(dec, caller, backend, critic=critic).ainvoke({"query": dec["original_query"]})

    assert len(critic.users) == 1 and len(backend.prompts) == 1
    assert state["review"].rounds == [] and len(caller.calls) == 1


@pytest.mark.parametrize("text", ["이건 JSON이 아님", '{"complete": false}', ""])
async def test_broken_critic_output_keeps_the_answer(text):
    caller = Caller(get_supplier=lambda args, n: SUPPLIER)
    backend = CiteAll()
    dec = price_question()
    state = await app_for(dec, caller, backend, critic=CriticSays(text=text)).ainvoke({"query": dec["original_query"]})

    assert state["answer"].kind == "answer" and state["review"].rounds == [] and len(backend.prompts) == 1


async def test_follow_up_that_duplicates_an_existing_sub_query_is_not_added():
    caller = Caller(get_supplier=lambda args, n: SUPPLIER)
    backend = CiteAll()
    dec = price_question()
    critic = CriticSays(("협력사 정보가 부족함", "SUP-0060 정보 알려줘"))
    state = await app_for(dec, caller, backend, critic=critic, follow=price_question()).ainvoke(
        {"query": dec["original_query"]})

    action = state["review"].rounds[0].actions[0]
    assert (action.kind, action.resolved, action.detail["added"]) == ("follow_up", False, [])
    assert len(caller.calls) == 1 and len(backend.prompts) == 1
    assert len(state["decomposition"].sub_queries) == 1


async def test_follow_up_that_finds_nothing_does_not_regenerate():
    caller = Caller(get_supplier=lambda args, n: SUPPLIER, search_regulation=lambda args, n: [])
    backend = CiteAll()
    dec = price_question()
    critic = CriticSays(("조정 규정 없음", "원자재 가격이 오를 때 하도급대금 조정 요건은?"))
    state = await app_for(dec, caller, backend, critic=critic, follow=law_follow_up()).ainvoke(
        {"query": dec["original_query"]})

    assert state["review"].rounds[0].actions[0].resolved is False
    assert not state["review"].rounds[0].regenerated and len(backend.prompts) == 1
    assert len(state["decomposition"].sub_queries) == 1   # 바뀐 것이 없으면 분해도 그대로


async def test_critic_runs_only_when_rules_found_nothing_and_there_is_an_answer():
    critic = CriticSays(("x", "원자재 가격이 오를 때 하도급대금 조정 요건은?"))
    caller = Caller(search_suppliers=lambda args, n: [])
    dec = decomposition(sub(entities={"process": "프레스", "material": "SPCC"}))
    state = await app_for(dec, caller, CiteAll(), critic=critic).ainvoke({"query": dec["original_query"]})
    assert state["answer"].kind == "no_result" and critic.users == []


def test_follow_ups_respect_the_sub_query_limit():
    critique = Critique.model_validate({"complete": False, "missing": [{"aspect": "a", "question": "b"}]})
    one = DecompositionResult.model_validate(decomposition(sub()))
    five = DecompositionResult.model_validate(decomposition(*(sub(f"q{i}") for i in range(1, 6))))
    assert [a.kind for a in follow_ups(critique, one)] == ["follow_up"]
    assert follow_ups(critique, five) == []
    assert follow_ups(Critique(complete=True, missing=[]), one) == []


async def test_critic_caps_follow_ups_and_ignores_missing_when_complete():
    many = json.dumps({"complete": False, "missing": [{"aspect": str(i), "question": f"질문 {i}"} for i in range(4)]})
    dec = DecompositionResult.model_validate(price_question())
    from negopilot_agent.generation.answer import Answer
    answer = Answer(kind="answer", text="답")
    got = await Critic(backend=CriticSays(text=many)).critique(dec, answer, {"q1": "ok"})
    assert [m.question for m in got.missing] == ["질문 0", "질문 1"]
    done = json.dumps({"complete": True, "missing": [{"aspect": "a", "question": "b"}]})
    assert (await Critic(backend=CriticSays(text=done)).critique(dec, answer, {})).missing == []
    assert "<answer>\n답" in build_user_message(dec, answer, {"q1": "ok"})


# ---------- 루프 제어 ----------

async def test_zero_rounds_keeps_the_old_single_pass_graph():
    caller = Caller(search_suppliers=search_by_region)
    dec = decomposition(sub(entities={"process": "프레스", "region": "제주"}))
    state = await app_for(dec, caller, CiteAll(), rounds=0).ainvoke({"query": dec["original_query"]})

    assert len(caller.calls) == 1
    assert state["answer"].kind == "no_result"
    assert state["review"].rounds == [] and state["review"].pending == []


async def test_rounds_are_capped():
    """보강해도 또 보강할 거리가 생기는 경우에도 라운드 수에서 멈춘다."""
    def flaky(args, n):
        if n == 1:
            raise ConnectionError("일시 오류")
        return SUPPLIER

    def make():
        return (Caller(get_supplier=flaky, **LAW_TOOLS),
                CriticSays(("조정 규정 없음", "원자재 가격이 오를 때 하도급대금 조정 요건은?")))

    dec = price_question()
    caller, critic = make()
    state = await app_for(dec, caller, CiteAll(), critic=critic, follow=law_follow_up()).ainvoke(
        {"query": dec["original_query"]})
    # 1회차 오류 재시도 뒤 검토자가 빠진 부분을 찾아도, 라운드가 1이라 더 보강하지 않는다
    assert len(state["review"].rounds) == 1 and critic.users == []
    assert [t for t, _ in caller.calls] == ["get_supplier", "get_supplier"]

    caller, critic = make()
    state = await app_for(dec, caller, CiteAll(), rounds=2, critic=critic, follow=law_follow_up()).ainvoke(
        {"query": dec["original_query"]})
    assert [r.actions[0].kind for r in state["review"].rounds] == ["retry_error", "follow_up"]
    assert [o.status for o in state["routing"].outcomes] == ["ok", "ok"]


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
async def test_http_empty_region_search_is_relaxed(monkeypatch):
    calls = []
    original = generation_stubs.GenerationStubToolCaller.__call__

    async def observe(caller, server, tool, args):
        calls.append((tool, dict(args)))
        return await original(caller, server, tool, args)

    monkeypatch.setattr(generation_stubs.GenerationStubToolCaller, "__call__", observe)
    app = create_app(settings=ApiSettings(mode="stub", internal_api_key=API_KEY, request_timeout_seconds=2))
    data = assert_wire(await post(app, payload("제주 협력사 찾아줘")))

    assert data["execution_status"] == "COMPLETED"
    assert data["answer"]["kind"] == "answer" and data["answer"]["citations"]
    assert any("제주에 없어 인접 지역" in n for n in data["answer"]["notices"])
    assert calls[0] == ("search_suppliers", {"region": "제주"})
    assert [c[1]["region"] for c in calls[1:]] == ["전남", "부산"]
    assert data["routing"]["outcomes"][0]["status"] == "ok"
    assert data["decomposition"]["assumptions"]
    assert data["decomposition"]["sub_queries"][0]["entities"]["region"] == "제주"
