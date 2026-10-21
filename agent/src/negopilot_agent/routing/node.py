"""LangGraph 노드: route.

    graph.add_node("route", make_route_node())
    graph.add_edge("decompose_query", "route")
    graph.add_edge("route", "generate")
"""

from typing import NotRequired, TypedDict

from negopilot_shared import DecompositionResult, RouteResult

from negopilot_agent.routing.mcp_client import McpToolCaller
from negopilot_agent.routing.router import Router, ToolCaller


class RoutingState(TypedDict):
    query: str
    decomposition: DecompositionResult
    routing: NotRequired[RouteResult]


def make_route_node(caller: ToolCaller | None = None):
    """테스트에서는 가짜 caller를 넣고, 생략하면 실제 MCP 서버(HTTP)를 부른다."""
    router = Router(caller or McpToolCaller())

    async def route(state: RoutingState) -> dict:
        return {"routing": await router.route(state["decomposition"])}

    return route
