"""LangSmith 추적 (선택). 기본은 꺼져 있다.

    LANGSMITH_TRACING=true
    LANGSMITH_API_KEY=...            # 각자 agent/.env에 넣는다. 커밋하지 않는다
    LANGSMITH_PROJECT=negopilot      # 선택. 없으면 LangSmith의 default 프로젝트

두 값이 다 있을 때만 켠다. 켜지면 그래프 실행 하나 아래에 노드(LangGraph가 기록), LLM 호출(OpenAI·Anthropic),
MCP 도구 호출이 함께 남는다. 질문과 협력사 데이터 및 법령 근거가 LangSmith로 전송되므로 필요할 때만 켠다.
입력·출력을 숨기려면 LANGSMITH_HIDE_INPUTS=true, LANGSMITH_HIDE_OUTPUTS=true (SDK 기본 기능).

꺼져 있으면 클라이언트를 감싸지 않고 기록도 만들지 않아서, 동작과 성능이 추적 도입 전과 같다.
"""

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import anthropic
import openai

_TRUE = {"1", "true", "yes", "on"}


def enabled() -> bool:
    """LANGSMITH_TRACING이 켜져 있고 API 키가 있을 때만 True."""
    return (os.getenv("LANGSMITH_TRACING", "").strip().lower() in _TRUE
            and bool(os.getenv("LANGSMITH_API_KEY", "").strip()))


def wrap_llm_client(client: Any) -> Any:
    """추적이 켜져 있으면 LLM 호출(입출력, 토큰)이 기록되도록 SDK 클라이언트를 감싼다."""
    if not enabled():
        return client
    from langsmith import wrappers

    if isinstance(client, openai.AsyncOpenAI):
        return wrappers.wrap_openai(client)
    if isinstance(client, anthropic.AsyncAnthropic):
        return wrappers.wrap_anthropic(client)
    return client


@contextmanager
def tool_span(server: str, tool: str, args: dict[str, Any]) -> Iterator[Callable[[Any], None]]:
    """MCP 도구 호출 하나를 tool 실행으로 기록한다. 결과는 돌려받은 함수로 남긴다. 예외는 오류로 기록된다."""
    if not enabled():
        yield lambda result: None
        return
    import langsmith

    with langsmith.trace(name=f"{server}.{tool}", run_type="tool", inputs=args,
                         metadata={"server": server, "tool": tool}) as run:
        yield lambda result: run.end(outputs={"result": result})


def run_config(request_id: str, mode: str) -> dict[str, Any]:
    """HTTP 실행 하나의 이름과 메타데이터. 추적이 꺼져 있으면 LangGraph가 무시한다."""
    return {"run_name": "negopilot.query", "tags": [f"mode:{mode}"],
            "metadata": {"request_id": request_id, "mode": mode}}
