"""NegoPilot Agent 그래프: decompose_query → route → (issue_review) → generate → review (→ research → generate).

    from negopilot_agent.graph import build_graph

    app = build_graph()
    state = await app.ainvoke({"query": "RFQ-2026-001 협력사 추천해줘"})
    state["answer"].text

분해가 clarify/failed면 도구를 부를 것이 없으므로 route를 건너뛰고 generate로 간다.
issue_review(쟁점 검토)는 NEGOPILOT_ISSUE_REVIEW=1이거나 issue_review=True일 때만 넣는다. 법령 하위 질문이 있으면
상황의 쟁점과 결론을 좌우하는 조문을 골라 근거를 보태고, 생성이 쟁점마다 그 조문을 인용하게 한다.
review는 답과 도구 결과를 보고 도구 일시 오류가 있으면 research로 다시 실행해 generate로 돌아가고,
검색 0건이면 답은 그대로 두고 결과가 있는 넓힌 검색(인접 지역 등)을 버튼으로 권한다.
라운드 수는 review_rounds(기본 NEGOPILOT_REVIEW_ROUNDS=1)로 정한다.
노드마다 부품(분해기, MCP 호출, 생성기)을 바꿔 끼울 수 있어서 테스트에서는 가짜를 넣는다.
"""

from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph

from negopilot_shared import DecompositionResult, RouteResult

from negopilot_agent.decomposition import QueryDecomposer, make_decompose_node
from negopilot_agent.generation import Answer, AnswerGenerator, make_generate_node
from negopilot_agent.issue_review import IssueReviewer, LegalIssue, make_issue_review_node, review_enabled
from negopilot_agent.review import (ReviewLog, after_research, after_review, make_research_node,
                                    make_review_node)
from negopilot_agent.routing import ToolCaller, make_route_node
from negopilot_agent.timing import timed_node


class AgentState(TypedDict):
    query: str                                   # 이번 사용자 질문
    history: NotRequired[list[dict]]             # [{"role": "user"|"assistant", "content": str}, ...]
    context: NotRequired[dict[str, list[str]]]    # 화면에서 선택한 기업 ID. 질문과 이력은 그대로 둔다
    decomposition: NotRequired[DecompositionResult]
    routing: NotRequired[RouteResult]
    issues: NotRequired[list[LegalIssue]]        # 쟁점 검토 결과 (issue_review를 넣었을 때만)
    answer: NotRequired[Answer]
    review: NotRequired[ReviewLog]               # 검토 루프 기록 (라운드별 보강과 결과)


def after_decompose(state: AgentState) -> str:
    return "route" if state["decomposition"].status == "ok" else "generate"


def build_graph(decomposer: QueryDecomposer | None = None, caller: ToolCaller | None = None,
                generator: AnswerGenerator | None = None, issue_review: bool | None = None,
                reviewer: IssueReviewer | None = None, review_rounds: int | None = None):
    """생략한 부품은 환경 변수 설정(.env.example)을 따른다. MCP 서버는 BUSINESS_MCP_URL, LAW_MCP_URL.

    issue_review: None이면 NEGOPILOT_ISSUE_REVIEW를 따른다. reviewer를 넣으면 쟁점 검토를 켠다(테스트용 가짜 모델).
    review_rounds: 검토 루프에서 보강할 최대 횟수. 0이면 보강하지 않는다 (예전 일직선 그래프와 같은 결과).
    """
    with_review = reviewer is not None or (review_enabled() if issue_review is None else issue_review)
    graph = StateGraph(AgentState)
    # timing.collect() 안에서 실행하면 노드별 시간이 기록된다 (밖이면 기록하지 않음)
    graph.add_node("decompose_query", timed_node("decompose_query", make_decompose_node(decomposer)))
    graph.add_node("route", timed_node("route", make_route_node(caller)))
    graph.add_node("generate", timed_node("generate", make_generate_node(generator)))
    graph.add_node("review", timed_node("review", make_review_node(review_rounds)))
    graph.add_node("research", timed_node("research", make_research_node(caller)))
    graph.add_edge(START, "decompose_query")
    graph.add_conditional_edges("decompose_query", after_decompose, ["route", "generate"])
    if with_review:
        graph.add_node("issue_review", timed_node("issue_review", make_issue_review_node(caller, reviewer)))
        graph.add_edge("route", "issue_review")
        graph.add_edge("issue_review", "generate")
    else:
        graph.add_edge("route", "generate")
    graph.add_edge("generate", "review")
    graph.add_conditional_edges("review", after_review, ["research", END])
    graph.add_conditional_edges("research", after_research, ["generate", END])
    return graph.compile()
