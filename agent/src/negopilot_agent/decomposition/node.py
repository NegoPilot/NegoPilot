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
    context: NotRequired[dict[str, list[str]]]    # 선택 기업 문맥. HTTP층에서 ID 형식을 검증한다
    decomposition: NotRequired[DecompositionResult]


def make_decompose_node(decomposer: QueryDecomposer | None = None):
    """테스트나 다른 설정으로 분해기를 바꿔 끼울 수 있게 노드를 만들어 돌려준다."""
    holder: dict[str, QueryDecomposer] = {}

    async def decompose_query(state: DecompositionState) -> dict:
        if "d" not in holder:
            holder["d"] = decomposer or QueryDecomposer()
        context = state.get("context")
        if context is None:
            # 문맥이 없는 기존 호출과 두 인자만 받는 테스트 대역의 계약을 유지한다.
            result = await holder["d"].decompose(state["query"], state.get("history"))
        else:
            result = await holder["d"].decompose(state["query"], state.get("history"), context=context)
        return {"decomposition": result}

    return decompose_query
