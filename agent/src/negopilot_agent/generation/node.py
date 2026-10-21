"""LangGraph 노드: generate.

    graph.add_node("generate", make_generate_node())
    graph.add_edge("route", "generate")
    graph.add_edge("generate", END)
"""

from typing import NotRequired, TypedDict

from negopilot_shared import DecompositionResult, RouteResult

from negopilot_agent.generation.answer import Answer
from negopilot_agent.generation.generator import AnswerGenerator


class GenerationState(TypedDict):
    query: str
    decomposition: DecompositionResult
    routing: NotRequired[RouteResult]     # 분해가 clarify/failed면 Routing 결과가 없을 수도 있다
    answer: NotRequired[Answer]


def make_generate_node(generator: AnswerGenerator | None = None):
    """테스트에서는 가짜 백엔드를 넣은 생성기를 넣고, 생략하면 환경 변수 설정을 따른다."""
    holder: dict[str, AnswerGenerator] = {}

    async def generate(state: GenerationState) -> dict:
        if "g" not in holder:
            holder["g"] = generator or AnswerGenerator()
        routing = state.get("routing") or RouteResult(status="not_routed")
        return {"answer": await holder["g"].generate(state["decomposition"], routing)}

    return generate
