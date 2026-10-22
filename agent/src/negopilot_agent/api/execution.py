"""Adapters around the unchanged QueryDecomposer and Router."""

import asyncio
import contextlib
from typing import Any, Protocol

import anthropic
import httpx2
import openai

from negopilot_shared import DecompositionResult, RouteResult, public_error_code
from negopilot_agent.routing import McpToolCaller, Router
from negopilot_agent.routing.router import ToolCaller

from .models import ErrorDetail, QueryExecutionRequest, QueryExecutionResponse
from .settings import ApiSettings
from .stubs import FixtureDecomposer, StubToolCaller


class Decomposer(Protocol):
    async def decompose(self, query: str, history: list[dict] | None = None) -> DecompositionResult: ...


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


def make_live_decomposer() -> Decomposer:
    # Construction is deliberately lazy so health and stub never need model credentials.
    from negopilot_agent.decomposition import DecomposerSettings, QueryDecomposer
    from negopilot_agent.decomposition.backends import OpenAIBackend

    settings = DecomposerSettings()
    try:
        if not settings.model.strip():
            raise ValueError("Model name must not be blank")
        if settings.backend == "anthropic":
            # Do not retry a transport failure at the HTTP boundary.
            client = anthropic.AsyncAnthropic(max_retries=0)
            return QueryDecomposer(settings=settings, client=client)
        if settings.backend == "openai":
            client = openai.AsyncOpenAI(max_retries=0)
            backend = OpenAIBackend(client, settings.model, settings.max_tokens)
            return QueryDecomposer(settings=settings, backend=backend)
        return QueryDecomposer(settings=settings)
    except (ValueError, anthropic.AnthropicError, openai.OpenAIError, httpx2.HTTPError, httpx2.InvalidURL):
        raise ModelUnavailable() from None


async def close_live_decomposer(decomposer: Decomposer) -> None:
    backend = getattr(decomposer, "backend", None)
    try:
        # After an execution timeout the enclosing task is already cancelling.
        # A second deadline bounds cleanup instead of delaying the 504 forever.
        async with asyncio.timeout(CLIENT_CLOSE_TIMEOUT_SECONDS):
            close = getattr(getattr(backend, "client", None), "close", None)
            if close is not None:
                await close()
            else:
                aclose = getattr(getattr(backend, "http", None), "aclose", None)
                if aclose is not None:
                    await aclose()
    except Exception:
        # Cleanup errors must not expose SDK internals or replace a completed result.
        pass


class QueryExecutor:
    def __init__(self, settings: ApiSettings, decomposer: Decomposer | None = None,
                 caller: ToolCaller | None = None) -> None:
        self.settings = settings
        self.decomposer = decomposer
        if settings.mode == "stub" and self.decomposer is None:
            self.decomposer = FixtureDecomposer()
        self.router = Router(SafeToolCaller(
            caller if caller is not None else StubToolCaller() if settings.mode == "stub" else McpToolCaller()
        ))

    async def execute(self, request: QueryExecutionRequest) -> QueryExecutionResponse:
        owned = self.decomposer is None
        decomposer = make_live_decomposer() if owned else self.decomposer
        try:
            decomposition = await decomposer.decompose(
                request.query, [entry.model_dump() for entry in request.history]
            )
            routing = public_routing(await self.router.route(decomposition))
            error = None
            if decomposition.status == "failed":
                status = "FAILED"
                error = ErrorDetail(code="DECOMPOSITION_FAILED", message="Query decomposition failed.", retryable=False)
            elif decomposition.status == "clarify":
                status = "NEEDS_INPUT"
            elif any(outcome.status == "error" for outcome in routing.outcomes):
                status = "FAILED"
                error = ErrorDetail(code="ROUTING_FAILED", message="Query routing failed.", retryable=False)
            elif any(outcome.status == "missing_args" for outcome in routing.outcomes):
                status = "NEEDS_INPUT"
            else:
                status = "COMPLETED"
            return QueryExecutionResponse(
                request_id=request.request_id, execution_status=status, mode=self.settings.mode,
                decomposition=decomposition, routing=routing, error=error,
            )
        finally:
            if owned:
                await close_live_decomposer(decomposer)
