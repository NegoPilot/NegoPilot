"""LangGraph 노드: decompose_query.

    graph.add_node("decompose_query", make_decompose_node())
    graph.add_edge(START, "decompose_query")
    graph.add_edge("decompose_query", "route")
"""

from typing import NotRequired, TypedDict

from negopilot_shared import DecompositionResult

from negopilot_agent.decomposition.decomposer import QueryDecomposer


class DecompositionState(TypedDict):
    query: str                                   # 이번 사용자 질문
    history: NotRequired[list[dict]]             # [{"role": "user"|"assistant", "content": str}, ...]
    decomposition: NotRequired[DecompositionResult]


def make_decompose_node(decomposer: QueryDecomposer | None = None):
    """테스트나 다른 설정으로 분해기를 바꿔 끼울 수 있게 노드를 만들어 돌려준다."""
    holder: dict[str, QueryDecomposer] = {}

    async def decompose_query(state: DecompositionState) -> dict:
        if "d" not in holder:
            holder["d"] = decomposer or QueryDecomposer()
        result = await holder["d"].decompose(state["query"], state.get("history"))
        return {"decomposition": result}

    return decompose_query
