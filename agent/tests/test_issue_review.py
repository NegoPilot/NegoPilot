"""쟁점 검토 노드 테스트: route → issue_review → generate.

모델과 서버 없이 돈다. 쟁점 선택은 가짜 백엔드가 정해 둔 JSON을 돌려주고, law-mcp는 가짜 caller가 답한다.

    uv run python -m pytest -q tests/test_issue_review.py
"""

import asyncio
import json

from negopilot_shared import DecompositionResult, RouteResult, SubQuery, SubQueryOutcome, ToolCall

from negopilot_agent.decomposition import QueryDecomposer
from negopilot_agent.generation import AnswerGenerator
from negopilot_agent.generation.prompt import build_user_message
from negopilot_agent.graph import build_graph
from negopilot_agent.issue_review import IssueReviewer, LegalIssue

from tests.test_graph import CiteFirst, FixedDecomposition, ReplayCaller, fixture

TOC = {"law_key": "subcontract_act", "law_name": "하도급거래 공정화에 관한 법률", "effective_date": "2026-10-02",
       "articles": [{"article_no": "제2조", "title": "정의", "chapter": "제1장"},
                    {"article_no": "제3조", "title": "서면의 발급 및 서류의 보존", "chapter": "제2장"},
                    {"article_no": "제4조", "title": "부당한 하도급대금의 결정 금지", "chapter": "제2장"},
                    {"article_no": "제11조", "title": "감액금지", "chapter": "제2장"}]}


def article(no: str) -> dict:
    return {"law_key": "subcontract_act", "law_name": TOC["law_name"], "article_no": no, "title": "", "chapter": "",
            "deleted": False, "paragraphs": [{"no": "①", "text": f"{no} 본문"}],
            "citation": f"「하도급거래 공정화에 관한 법률」 {no}", "effective_date": "2026-10-02", "as_of": "2026-10-25",
            "source_url": "https://www.law.go.kr/", "upcoming_changes": []}


class FakeLaw:
    def __init__(self, fail: set[str] = frozenset()):
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail

    async def __call__(self, server, tool, args):
        self.calls.append((tool, args))
        if tool in self.fail:
            raise RuntimeError("law-mcp 꺼짐")
        if tool == "list_articles":
            return TOC
        if tool == "get_article":
            return article(args["article_no"])
        raise AssertionError(f"예상 밖 호출 {server}.{tool}")


class Picks:
    """쟁점 선택 백엔드 대신 정해 둔 출력을 돌려준다. 받은 입력을 남긴다."""

    def __init__(self, issues: list[dict] | None):
        self.text = None if issues is None else json.dumps({"issues": issues}, ensure_ascii=False)
        self.users: list[str] = []

    async def generate(self, system: str, user: str) -> str | None:
        self.users.append(user)
        return self.text


def pick(no: str, issue: str = "쟁점") -> dict:
    return {"issue": issue, "article_no": no, "reason": "상황의 사실이 이 조문에 걸림"}


def decomposition(*intents: str) -> DecompositionResult:
    return DecompositionResult(status="ok", original_query="연동 대상 원재료인데 단가를 그대로 둬도 돼?", sub_queries=[
        SubQuery(id=f"q{n}", question=f"질문 {n}", original_text="원문", intent=intent, grounding="required",
                 confidence=0.9) for n, intent in enumerate(intents, 1)])


def routed(d: DecompositionResult) -> RouteResult:
    found = ToolCall(server="law-mcp", tool="get_article", args={"law": "하도급법", "article_no": "제4조"},
                     result=article("제4조"))
    return RouteResult(status="routed", outcomes=[SubQueryOutcome(id=sq.id, intent=sq.intent, status="ok",
                                                                  calls=[found]) for sq in d.sub_queries])


def review(reviewer: IssueReviewer, d: DecompositionResult):
    return asyncio.run(reviewer.review(d, routed(d)))


def test_review_adds_issue_sub_query_with_fetched_articles():
    law = FakeLaw()
    backend = Picks([pick("제2조 정의", "연동 대상 정의"), pick("제4조"), pick("제4조"), pick("제99조"), pick("제3조"),
                     pick("제11조")])
    d = decomposition("law_check")
    routing, new_d, issues = review(IssueReviewer(law, backend), d)

    # 번호 정규화, 중복·목차 밖 조문 제거, 최대 3개
    assert [i.article_no for i in issues] == ["제2조", "제4조", "제3조"]
    assert issues[0].title == "정의" and issues[0].issue == "연동 대상 정의"
    added = new_d.sub_queries[-1]
    assert added.id == "q2" and added.intent == "law_check" and "제2조 정의" in added.question
    assert routing.outcomes[-1].id == "q2"
    assert [c.args["article_no"] for c in routing.outcomes[-1].calls] == ["제2조", "제4조", "제3조"]
    # 모델은 이미 찾은 조문과 목차를 본다
    sent = json.loads(backend.users[0])
    assert sent["이미 찾은 조문"] == ["제4조 부당한 하도급대금의 결정 금지"]
    assert "제11조 감액금지" in sent["하도급법 목차"]


def test_review_skips_without_legal_sub_query():
    law = FakeLaw()
    d = decomposition("supplier_ranking", "law_article")
    routing, new_d, issues = review(IssueReviewer(law, Picks([pick("제4조")])), d)
    assert issues == [] and new_d is d and law.calls == []


def test_review_skips_when_sub_queries_are_full():
    law = FakeLaw()
    d = decomposition(*["law_check"] * 5)
    _, new_d, issues = review(IssueReviewer(law, Picks([pick("제4조")])), d)
    assert issues == [] and len(new_d.sub_queries) == 5 and law.calls == []


def test_review_failures_leave_routing_unchanged():
    d = decomposition("law_check")
    for law, backend in ((FakeLaw(fail={"list_articles"}), Picks([pick("제4조")])),   # 목차 실패
                         (FakeLaw(), Picks(None)),                                  # 모델 거절
                         (FakeLaw(fail={"get_article"}), Picks([pick("제2조")]))):   # 조문 조회 실패
        routing, new_d, issues = review(IssueReviewer(law, backend), d)
        assert issues == [] and new_d is d and len(routing.outcomes) == 1


def test_review_passes_as_of_to_law_mcp():
    law = FakeLaw()
    d = decomposition("offer_check")
    d.sub_queries[0].entities.as_of = "2026-10-23"
    review(IssueReviewer(law, Picks([pick("제4조")])), d)
    assert all(args.get("as_of") == "2026-10-23" for _, args in law.calls)


def test_user_message_lists_issues_after_evidence():
    issue = LegalIssue(issue="연동 대상 정의", article_no="제2조", title="정의", reason="r")
    text = build_user_message(decomposition("law_check"), [], [], issues=[issue])
    assert "<review_issues>" in text and "- 연동 대상 정의: 하도급법 제2조 (정의)" in text
    assert text.index("<evidence>") < text.index("<review_issues>")
    assert "<review_issues>" not in build_user_message(decomposition("law_check"), [], [])


class SeenByGenerator(CiteFirst):
    def __init__(self):
        self.users: list[str] = []

    async def generate(self, system: str, user: str) -> str:
        self.users.append(user)
        return await super().generate(system, user)


def test_graph_runs_issue_review_between_route_and_generate():
    dec = fixture("decomposition", "03_rank_then_law")
    replay = ReplayCaller(fixture("routing", "03_rank_then_law")["routing"])
    law = FakeLaw()

    async def caller(server, tool, args):   # Routing은 기록을, 쟁점 검토는 가짜 law-mcp를 쓴다
        return await (law(server, tool, args) if tool == "list_articles" or "article_no" in args and
                      args.get("article_no") == "제11조" else replay(server, tool, args))

    gen = SeenByGenerator()
    app = build_graph(decomposer=QueryDecomposer(backend=FixedDecomposition(dec["expected"])), caller=caller,
                      generator=AnswerGenerator(backend=gen),
                      reviewer=IssueReviewer(caller, Picks([pick("제11조", "단가 5% 인하가 감액인지")])))
    state = asyncio.run(app.ainvoke({"query": dec["query"]}))

    assert [i.article_no for i in state["issues"]] == ["제11조"]
    assert state["decomposition"].sub_queries[-1].id == "q3"
    assert state["routing"].outcomes[-1].calls[0].args["article_no"] == "제11조"
    assert "- 단가 5% 인하가 감액인지: 하도급법 제11조 (감액금지)" in gen.users[0]
    assert state["answer"].kind == "answer"


def test_graph_without_issue_review_is_unchanged():
    app = build_graph(decomposer=QueryDecomposer(backend=FixedDecomposition({})), issue_review=False)
    assert "issue_review" not in app.get_graph().nodes
