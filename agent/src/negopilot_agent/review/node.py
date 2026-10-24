"""LangGraph 노드: review, research.

    graph.add_edge("generate", "review")
    graph.add_conditional_edges("review", after_review, ["research", END])
    graph.add_conditional_edges("research", after_research, ["generate", END])

review는 답을 보고 보강할 거리를 정하고(pending), research는 그것을 실행해 Routing 결과를 고친다.
규칙(plan)에 걸린 것이 없고 답이 나왔으면 모델 검토자(Critic)가 빠진 부분을 찾아 보충 질문을 낸다.
보강으로 바뀐 것이 있으면 generate가 답을 다시 쓰고, review가 한 번 더 본다. 라운드 수를 넘으면 끝낸다.
"""

import logging
from typing import NotRequired, TypedDict

from langgraph.graph import END

from negopilot_shared import DecompositionResult, RouteResult

from negopilot_agent.generation.answer import Answer
from negopilot_agent.decomposition import QueryDecomposer
from negopilot_agent.review.critic import Critic, critic_enabled
from negopilot_agent.review.reviewer import (Decomposer, ReviewLog, ReviewRound, configured_rounds, follow_ups, plan,
                                             reinforce)
from negopilot_agent.routing.mcp_client import McpToolCaller
from negopilot_agent.routing.router import Router, ToolCaller

log = logging.getLogger(__name__)


class ReviewState(TypedDict):
    query: str
    decomposition: DecompositionResult
    routing: NotRequired[RouteResult]
    answer: NotRequired[Answer]
    review: NotRequired[ReviewLog]


def make_review_node(max_rounds: int | None = None, critic: Critic | bool | None = None):
    """max_rounds를 생략하면 NEGOPILOT_REVIEW_ROUNDS(기본 1)를 따른다. 0이면 검토만 하고 보강하지 않는다.

    critic: 모델 검토자. None이면 NEGOPILOT_REVIEW_CRITIC(기본 끔)을 따르고, False면 규칙 검토만 한다.
    """
    rounds = configured_rounds() if max_rounds is None else max_rounds
    if critic is None:
        critic = critic_enabled()
    holder: dict[str, Critic] = {} if critic is True else {"c": critic} if critic else {}

    async def review(state: ReviewState) -> dict:
        history = state.get("review") or ReviewLog()
        if len(history.rounds) >= rounds:
            return {"review": history.model_copy(update={"pending": []})}
        tried = history.tried()
        decomposition, routing, answer = state["decomposition"], state.get("routing"), state.get("answer")
        # 이미 해 본 보강은 다시 하지 않는다 (같은 실패를 되풀이하지 않게)
        pending = [a for a in plan(decomposition, routing, answer) if a.key() not in tried]
        if (not pending and critic and answer is not None and answer.kind == "answer"
                and routing is not None and routing.status == "routed"):
            if "c" not in holder:
                holder["c"] = Critic()
            critique = await holder["c"].critique(decomposition, answer, {o.id: o.status for o in routing.outcomes})
            pending = [a for a in follow_ups(critique, decomposition) if a.key() not in tried]
        if pending:
            log.info("검토: 보강 %s", [(a.kind, a.sub_query, a.reason) for a in pending])
        return {"review": history.model_copy(update={"pending": pending})}

    return review


def after_review(state: ReviewState) -> str:
    return "research" if state["review"].pending else END


def make_research_node(caller: ToolCaller | None = None, decomposer: Decomposer | None = None):
    """테스트에서는 가짜 caller를 넣고, 생략하면 실제 MCP 서버(HTTP)를 부른다.

    decomposer는 검토자의 보충 질문을 하위 질문으로 바꿀 때 쓴다. 생략하면 보충이 필요할 때 기본 분해기를 만든다.
    """
    router = Router(caller or McpToolCaller())
    holder: dict[str, Decomposer] = {"d": decomposer} if decomposer else {}

    async def research(state: ReviewState) -> dict:
        history = state["review"]
        if "d" not in holder and any(a.kind == "follow_up" for a in history.pending):
            holder["d"] = QueryDecomposer()
        decomposition, routing, actions, changed = await reinforce(
            router, holder.get("d"), state["decomposition"], state["routing"], history.pending)
        done = ReviewRound(round=len(history.rounds) + 1, actions=actions, regenerated=changed)
        log.info("검토 %d회차: %s", done.round, [(a.kind, a.sub_query, a.resolved) for a in actions])
        out: dict = {"review": ReviewLog(rounds=[*history.rounds, done])}
        if changed:
            out |= {"decomposition": decomposition, "routing": routing}
        return out

    return research


def after_research(state: ReviewState) -> str:
    return "generate" if state["review"].rounds[-1].regenerated else END
