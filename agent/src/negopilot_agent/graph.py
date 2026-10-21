"""NegoPilot Agent 그래프: decompose_query → route → generate.

    from negopilot_agent.graph import build_graph

    app = build_graph()
    state = await app.ainvoke({"query": "RFQ-2026-001 협력사 추천해줘"})
    state["answer"].text

분해가 clarify/failed면 도구를 부를 것이 없으므로 route를 건너뛰고 generate로 간다.
노드마다 부품(분해기, MCP 호출, 생성기)을 바꿔 끼울 수 있어서 테스트에서는 가짜를 넣는다.
"""

from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph

from negopilot_shared import DecompositionResult, RouteResult

from negopilot_agent.decomposition import QueryDecomposer, make_decompose_node
from negopilot_agent.generation import Answer, AnswerGenerator, make_generate_node
from negopilot_agent.routing import ToolCaller, make_route_node


class AgentState(TypedDict):
    query: str                                   # 이번 사용자 질문
    history: NotRequired[list[dict]]             # [{"role": "user"|"assistant", "content": str}, ...]
    decomposition: NotRequired[DecompositionResult]
    routing: NotRequired[RouteResult]
    answer: NotRequired[Answer]


def after_decompose(state: AgentState) -> str:
    return "route" if state["decomposition"].status == "ok" else "generate"


def build_graph(decomposer: QueryDecomposer | None = None, caller: ToolCaller | None = None,
                generator: AnswerGenerator | None = None):
    """생략한 부품은 환경 변수 설정(.env.example)을 따른다. MCP 서버는 BUSINESS_MCP_URL, LAW_MCP_URL."""
    graph = StateGraph(AgentState)
    graph.add_node("decompose_query", make_decompose_node(decomposer))
    graph.add_node("route", make_route_node(caller))
    graph.add_node("generate", make_generate_node(generator))
    graph.add_edge(START, "decompose_query")
    graph.add_conditional_edges("decompose_query", after_decompose, ["route", "generate"])
    graph.add_edge("route", "generate")
    graph.add_edge("generate", END)
    return graph.compile()
