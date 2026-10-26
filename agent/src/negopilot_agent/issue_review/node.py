"""LangGraph 노드: issue_review (쟁점 검토).

    graph.add_node("issue_review", make_issue_review_node(caller))
    graph.add_edge("route", "issue_review")
    graph.add_edge("issue_review", "generate")
"""

from typing import NotRequired, TypedDict

from negopilot_shared import DecompositionResult, RouteResult

from negopilot_agent.issue_review.reviewer import IssueReviewer, LegalIssue
from negopilot_agent.routing.mcp_client import McpToolCaller
from negopilot_agent.routing.router import ToolCaller


class IssueReviewState(TypedDict):
    query: str
    decomposition: DecompositionResult
    routing: NotRequired[RouteResult]
    issues: NotRequired[list[LegalIssue]]


def make_issue_review_node(caller: ToolCaller | None = None, reviewer: IssueReviewer | None = None):
    """reviewer를 넣으면 그대로 쓰고(테스트), 생략하면 caller로 law-mcp를 부르는 기본 검토기를 쓴다."""
    reviewer = reviewer or IssueReviewer(caller or McpToolCaller())

    async def issue_review(state: IssueReviewState) -> dict:
        routing = state.get("routing")
        if routing is None:
            return {}
        routing, decomposition, issues = await reviewer.review(state["decomposition"], routing)
        return {"routing": routing, "decomposition": decomposition, "issues": issues}

    return issue_review
