"""NegoPilot Agent 그래프: decompose_query → route → generate → review (→ research → generate).

    from negopilot_agent.graph import build_graph

    app = build_graph()
    state = await app.ainvoke({"query": "RFQ-2026-001 협력사 추천해줘"})
    state["answer"].text

분해가 clarify/failed면 도구를 부를 것이 없으므로 route를 건너뛰고 generate로 간다.
review는 답과 도구 결과를 보고 근거를 보강할 거리(도구 일시 오류, 검색 0건, 모델 검토자가 찾은 빠진 부분)가 있으면
research로 보강한 뒤 generate로 돌아간다. 라운드 수는 review_rounds(기본 NEGOPILOT_REVIEW_ROUNDS=1)로 정한다.
노드마다 부품(분해기, MCP 호출, 생성기)을 바꿔 끼울 수 있어서 테스트에서는 가짜를 넣는다.
"""

from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph

from negopilot_shared import DecompositionResult, RouteResult

from negopilot_agent.decomposition import QueryDecomposer, make_decompose_node
from negopilot_agent.generation import Answer, AnswerGenerator, make_generate_node
from negopilot_agent.review import (Critic, ReviewLog, after_research, after_review, make_research_node,
                                    make_review_node)
from negopilot_agent.routing import ToolCaller, make_route_node
from negopilot_agent.timing import timed_node


class AgentState(TypedDict):
    query: str                                   # 이번 사용자 질문
    history: NotRequired[list[dict]]             # [{"role": "user"|"assistant", "content": str}, ...]
    context: NotRequired[dict[str, list[str]]]    # 화면에서 선택한 기업 ID. 질문과 이력은 그대로 둔다
    decomposition: NotRequired[DecompositionResult]
    routing: NotRequired[RouteResult]
    answer: NotRequired[Answer]
    review: NotRequired[ReviewLog]               # 검토 루프 기록 (라운드별 보강과 결과)


def after_decompose(state: AgentState) -> str:
    return "route" if state["decomposition"].status == "ok" else "generate"


def build_graph(decomposer: QueryDecomposer | None = None, caller: ToolCaller | None = None,
                generator: AnswerGenerator | None = None, review_rounds: int | None = None,
                critic: Critic | bool | None = None):
    """생략한 부품은 환경 변수 설정(.env.example)을 따른다. MCP 서버는 BUSINESS_MCP_URL, LAW_MCP_URL.

    review_rounds: 검토 루프에서 보강할 최대 횟수. 0이면 보강하지 않는다 (예전 일직선 그래프와 같은 결과).
    critic: 모델 검토자. None이면 NEGOPILOT_REVIEW_CRITIC(기본 켬), False면 규칙 검토만.
    """
    graph = StateGraph(AgentState)
    # timing.collect() 안에서 실행하면 노드별 시간이 기록된다 (밖이면 기록하지 않음)
    graph.add_node("decompose_query", timed_node("decompose_query", make_decompose_node(decomposer)))
    graph.add_node("route", timed_node("route", make_route_node(caller)))
    graph.add_node("generate", timed_node("generate", make_generate_node(generator)))
    graph.add_node("review", timed_node("review", make_review_node(review_rounds, critic)))
    graph.add_node("research", timed_node("research", make_research_node(caller, decomposer)))
    graph.add_edge(START, "decompose_query")
    graph.add_conditional_edges("decompose_query", after_decompose, ["route", "generate"])
    graph.add_edge("route", "generate")
    graph.add_edge("generate", "review")
    graph.add_conditional_edges("review", after_review, ["research", END])
    graph.add_conditional_edges("research", after_research, ["generate", END])
    return graph.compile()
