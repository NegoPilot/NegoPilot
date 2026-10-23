"""검토 루프 테스트: generate → review → research → generate.

모델과 MCP 서버 없이 가짜 분해·도구·생성으로 세 가지 보강을 확인한다.
- relax: 협력사 검색 0건 → 지역 등 조건을 빼고 다시 검색, 뺀 조건은 "가정:"으로 밝힘
- retry_error: 일시 오류로 실패한 도구 호출을 다시 실행 (공개 오류 코드가 붙은 입력 오류는 제외)
- fetch_article: 답이 근거에 없는 조문을 써서 가려지면 그 조문을 가져와 다시 생성

    uv run python -m pytest -q tests/test_review_loop.py
"""

import json
import re

import pytest

from negopilot_shared import DecompositionResult, RouteResult

from negopilot_agent.decomposition import QueryDecomposer
from negopilot_agent.generation import AnswerGenerator
from negopilot_agent.graph import build_graph
from negopilot_agent.review import ReviewLog, plan
from negopilot_agent.review.reviewer import guess_law
from negopilot_agent.routing import Router

from negopilot_agent.api import generation_stubs
from negopilot_agent.api.app import create_app
from negopilot_agent.api.settings import ApiSettings

from tests.test_generation_http_contract import (API_KEY, assert_wire, no_external_dependencies,  # noqa: F401
                                                 payload, post)

ROWS = [{"supplier_id": "SUP-0060", "name": "가상 프레스", "region": "경남", "process": "프레스"},
        {"supplier_id": "SUP-0052", "name": "가상 금형", "region": "울산", "process": "프레스"}]
ARTICLE_13 = {
    "law_key": "subcontract_act", "law_name": "하도급거래 공정화에 관한 법률", "article_no": "제13조",
    "title": "하도급대금의 지급 등", "chapter": None, "deleted": False,
    "paragraphs": [{"no": "①", "text": "원사업자는 목적물등의 수령일부터 60일 이내의 가능한 짧은 기한으로 정한 지급기일까지 하도급대금을 지급하여야 한다."}],
    "citation": "「하도급거래 공정화에 관한 법률」 제13조(하도급대금의 지급 등) [시행 2026-10-02]",
    "effective_date": "2026-10-02", "as_of": "2026-10-23",
    "source_url": "https://www.law.go.kr/법령/하도급거래공정화에관한법률", "upcoming_changes": [],
}
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


class SaysArticle13:
    """근거에 제13조가 없으면 기억으로 '하도급법 제13조'를 쓴다 (가려짐). 근거에 생기면 그것을 인용한다."""

    def __init__(self):
        self.prompts: list[str] = []

    async def generate(self, system: str, user: str) -> str:
        self.prompts.append(user)
        article = re.search(r'<e id="(E\d+)"[^>]*citable="yes" label="[^"]*제13조', user)
        first = re.search(r'<e id="(E\d+)"[^>]*citable="yes"', user).group(1)
        if article:
            eid = article.group(1)
            return json.dumps({"answer": f"결제 기한은 하도급법 제13조를 따릅니다. [{eid}]", "used_evidence": [eid]})
        return json.dumps({"answer": f"이 협력사의 결제 조건입니다. [{first}] 결제 기한은 하도급법 제13조를 따릅니다.",
                           "used_evidence": [first]})


def app_for(dec: dict, caller: Caller, generator_backend, rounds: int | None = 1):
    return build_graph(decomposer=QueryDecomposer(backend=Fixed(dec)), caller=caller,
                       generator=AnswerGenerator(backend=generator_backend), review_rounds=rounds)


# ---------- relax ----------

def search_by_region(args, _n):
    return [] if "region" in args else ROWS


async def test_empty_search_is_relaxed_and_the_dropped_condition_is_disclosed():
    caller = Caller(search_suppliers=search_by_region)
    backend = CiteAll()
    dec = decomposition(sub(entities={"process": "프레스", "region": "제주"}))
    state = await app_for(dec, caller, backend).ainvoke({"query": dec["original_query"]})

    assert [args for _, args in caller.calls] == [{"process": "프레스", "region": "제주"}, {"process": "프레스"}]
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


# ---------- fetch_article ----------

async def test_ungrounded_article_is_fetched_and_the_answer_rewritten():
    caller = Caller(get_supplier=lambda args, n: SUPPLIER, get_article=lambda args, n: ARTICLE_13)
    backend = SaysArticle13()
    dec = decomposition(sub(intent="supplier_detail", entities={"supplier": "SUP-0060"}), query="SUP-0060 결제 조건 알려줘")
    state = await app_for(dec, caller, backend).ainvoke({"query": dec["original_query"]})

    assert ("get_article", {"law": "subcontract_act", "article_no": "제13조"}) in caller.calls
    answer = state["answer"]
    assert answer.verification.ok and not answer.verification.masked
    assert "[확인 필요]" not in answer.text and "제13조" in answer.text
    assert any("제13조" in c.label for c in answer.citations)
    # 처음 생성(재시도 포함 2회) + 보강 뒤 1회
    assert len(backend.prompts) == 3
    action = state["review"].rounds[0].actions[0]
    assert (action.kind, action.resolved, action.detail) == (
        "fetch_article", True, {"law": "subcontract_act", "article_no": "제13조"})


async def test_unknown_article_keeps_the_masked_answer_without_regenerating():
    def no_article(args, n):
        raise ValueError("없는 조문")

    caller = Caller(get_supplier=lambda args, n: SUPPLIER, get_article=no_article)
    backend = SaysArticle13()
    dec = decomposition(sub(intent="supplier_detail", entities={"supplier": "SUP-0060"}), query="SUP-0060 결제 조건 알려줘")
    state = await app_for(dec, caller, backend).ainvoke({"query": dec["original_query"]})

    assert state["answer"].verification.masked and "[확인 필요]" in state["answer"].text
    assert len(backend.prompts) == 2
    assert state["review"].rounds[0].regenerated is False


def law_routing(*laws: str) -> RouteResult:
    return RouteResult.model_validate({"status": "routed", "outcomes": [
        {"id": "q1", "intent": "law_check", "status": "ok", "calls": [
            {"server": "law-mcp", "tool": "search_regulation", "args": {"query": "x"},
             "result": [{"law_key": law, "article_no": "제1조", "type": "조문"} for law in laws]}]}]})


@pytest.mark.parametrize("text,laws,expected", [
    ("상생협력법 [확인 필요]에 따라", ("subcontract_act",), "winwin_act"),         # 가린 자리 앞 이름이 먼저
    ("하도급법 시행령 [확인 필요]", (), "subcontract_decree"),
    ("관련 조문 [확인 필요]", ("winwin_act",), "winwin_act"),                       # 근거의 법령
    ("관련 조문 [확인 필요]", (), "subcontract_act"),                                # 단서가 없으면 하도급법
    ("관련 조문 [확인 필요]", ("winwin_act", "subcontract_act"), None),              # 모르면 가져오지 않는다
    ("하도급법 [확인 필요], 상생협력법 [확인 필요]", (), None),
])
def test_law_of_masked_article(text, laws, expected):
    assert guess_law(text, law_routing(*laws)) == expected


# ---------- 루프 제어 ----------

async def test_zero_rounds_keeps_the_old_single_pass_graph():
    caller = Caller(search_suppliers=search_by_region)
    dec = decomposition(sub(entities={"process": "프레스", "region": "제주"}))
    state = await app_for(dec, caller, CiteAll(), rounds=0).ainvoke({"query": dec["original_query"]})

    assert len(caller.calls) == 1
    assert state["answer"].kind == "no_result"
    assert state["review"].rounds == [] and state["review"].pending == []


async def test_rounds_are_capped(monkeypatch):
    """보강해도 또 보강할 거리가 생기는 경우에도 라운드 수에서 멈춘다."""
    def flaky(args, n):
        if n == 1:
            raise ConnectionError("일시 오류")
        return SUPPLIER

    caller = Caller(get_supplier=flaky, get_article=lambda args, n: ARTICLE_13)
    backend = SaysArticle13()
    dec = decomposition(sub(intent="supplier_detail", entities={"supplier": "SUP-0060"}), query="SUP-0060 결제 조건 알려줘")
    state = await app_for(dec, caller, backend).ainvoke({"query": dec["original_query"]})

    # 1회차 오류 재시도 뒤 새 답에서 조문이 가려져도, 라운드가 1이라 더 보강하지 않는다
    assert len(state["review"].rounds) == 1
    assert [t for t, _ in caller.calls] == ["get_supplier", "get_supplier"]
    assert state["answer"].verification.masked

    caller = Caller(get_supplier=flaky, get_article=lambda args, n: ARTICLE_13)
    state = await app_for(dec, caller, SaysArticle13(), rounds=2).ainvoke({"query": dec["original_query"]})
    assert [r.actions[0].kind for r in state["review"].rounds] == ["retry_error", "fetch_article"]
    assert state["answer"].verification.ok


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
    assert any("지역(제주) 조건을 빼고 다시 찾았습니다" in n for n in data["answer"]["notices"])
    assert calls == [("search_suppliers", {"region": "제주"}), ("search_suppliers", {})]
    assert data["routing"]["outcomes"][0]["status"] == "ok"
    assert data["decomposition"]["assumptions"]
