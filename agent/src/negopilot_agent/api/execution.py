"""Tool-call and routing sanitizers shared by the Generation HTTP adapter."""

import contextlib
from typing import Any

from negopilot_shared import RouteResult, public_error_code
from negopilot_agent.routing.router import ToolCaller


class ModelUnavailable(Exception):
    pass


class ToolExecutionError(Exception):
    pass


SAFE_TOOL_ERROR = "Tool execution failed."
SAFE_ROUTING_MESSAGE = "Routing details are unavailable."
SAFE_HALT_REASON = "Routing halted because a dependency returned no results."
CLIENT_CLOSE_TIMEOUT_SECONDS = 0.1


class SafeToolCaller:
    """Prevent Router from embedding third-party exception text in its result."""

    def __init__(self, caller: ToolCaller) -> None:
        self.caller = caller

    async def __call__(self, server: str, tool: str, args: dict[str, Any]) -> Any:
        try:
            return await self.caller(server, tool, args)
        except Exception as exc:
            # 원문은 가리고, 공개 목록에 있는 오류 코드만 남긴다 (docs/contracts/check-offer.md 3절)
            code = public_error_code(str(exc))
            raise ToolExecutionError(f"[{code}] {SAFE_TOOL_ERROR}" if code else SAFE_TOOL_ERROR) from None

    def session(self):
        """감싼 caller의 연결 재사용을 그대로 쓴다 (McpToolCaller.session)."""
        session = getattr(self.caller, "session", None)
        return session() if session else contextlib.nullcontext()


def public_routing(result: RouteResult) -> RouteResult:
    """Sanitize diagnostic fields on a copy while retaining evidence and results."""
    result = result.model_copy(deep=True)
    for outcome in result.outcomes:
        for call in (*outcome.calls, *outcome.referenced_articles):
            if call.error is not None:
                call.error = SAFE_TOOL_ERROR
        if outcome.message is not None:
            outcome.message = SAFE_ROUTING_MESSAGE
    if result.halt_reason is not None:
        result.halt_reason = SAFE_HALT_REASON
    return result
