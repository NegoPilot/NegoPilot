"""Run one complete Agent graph with request-owned lazy model clients."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

import anthropic
import httpx2
import openai

from negopilot_shared import DecompositionResult, RouteResult
from negopilot_agent.decomposition import DecomposerSettings, QueryDecomposer
from negopilot_agent.decomposition.backends import AnthropicBackend, Backend, OpenAIBackend, OllamaBackend
from negopilot_agent.generation import Answer, AnswerGenerator, GeneratorSettings
from negopilot_agent.generation.backends import GenerationAnthropicBackend, GenerationOpenAIBackend, GenerationOllamaBackend
from negopilot_agent import timing, tracing
from negopilot_agent.graph import build_graph
from negopilot_agent.review import Critic, critic_enabled
from negopilot_agent.routing import McpToolCaller, ToolCaller

from .execution import CLIENT_CLOSE_TIMEOUT_SECONDS, ModelUnavailable, SafeToolCaller, public_routing
from .generation_models import GenerationExecutionRequest, GenerationExecutionResponse
from .generation_stubs import FixtureDecompositionBackend, FixtureGenerationBackend, GenerationStubToolCaller
from .models import ErrorDetail
from .settings import ApiSettings

log = logging.getLogger(__name__)


class ContextDecomposer(Protocol):
    async def decompose(self, query: str, history: list[dict] | None = None, *,
                        context: dict[str, list[str]] | None = None) -> DecompositionResult: ...


class LazyModelBackend:
    """Create a client on the first model call and close only that owned client."""

    def __init__(self, phase: Literal["decomposition", "generation"]) -> None:
        self.phase = phase
        # 분해기가 프롬프트 출력 형식을 정할 때 읽는다 (openai 분해는 짧은 형식). 클라이언트는 만들지 않는다
        self.COMPACT_OUTPUT = phase == "decomposition" and DecomposerSettings().backend == "openai"
        self._backend: Backend | None = None
        self._close: Callable[[], Awaitable[None]] | None = None

    def _create_backend(self) -> Backend:
        generation = self.phase == "generation"
        settings = GeneratorSettings() if generation else DecomposerSettings()
        try:
            if not settings.model.strip():
                raise ValueError("Model name must not be blank")
            if settings.backend == "ollama":
                backend_type = GenerationOllamaBackend if generation else OllamaBackend
                backend = backend_type.from_env(settings.model, settings.max_tokens)
                self._close = backend.http.aclose
                return backend
            if settings.backend == "anthropic":
                client = anthropic.AsyncAnthropic(max_retries=0)
                self._close = client.close
                backend_type = GenerationAnthropicBackend if generation else AnthropicBackend
                return backend_type(client, settings.model, settings.effort, settings.max_tokens,
                                    settings.use_fallbacks)
            if settings.backend == "openai":
                client = openai.AsyncOpenAI(max_retries=0)
                self._close = client.close
                backend_type = GenerationOpenAIBackend if generation else OpenAIBackend
                return backend_type(client, settings.model, settings.max_tokens)
            raise ValueError("Unknown model backend")
        except (ValueError, anthropic.AnthropicError, openai.OpenAIError, httpx2.HTTPError, httpx2.InvalidURL):
            raise ModelUnavailable() from None

    async def generate(self, system: str, user: str) -> str | None:
        if self._backend is None:
            self._backend = self._create_backend()
        return await self._backend.generate(system, user)

    async def aclose(self) -> None:
        # Reading AnswerGenerator.backend here would create an unused client.
        # The bound closer is registered only when this wrapper creates a client.
        close, self._close = self._close, None
        if close is not None:
            try:
                async with asyncio.timeout(CLIENT_CLOSE_TIMEOUT_SECONDS):
                    await close()
            except Exception:
                pass


class SafeGenerator:
    """Keep raw routing diagnostics out of the core Generator's logs."""

    def __init__(self, generator: AnswerGenerator) -> None:
        self.generator = generator

    async def generate(self, decomposition: DecompositionResult, routing: RouteResult) -> Answer:
        return await self.generator.generate(decomposition, public_routing(routing))


class GenerationExecutor:
    def __init__(self, settings: ApiSettings, decomposer: ContextDecomposer | None = None,
                 caller: ToolCaller | None = None, backend: Backend | None = None) -> None:
        self.settings = settings
        self.decomposer = decomposer
        self.caller = caller
        self.backend = backend

    async def execute(self, request: GenerationExecutionRequest) -> GenerationExecutionResponse:
        owned: list[LazyModelBackend | Critic] = []
        try:
            decomposer = self.decomposer
            if decomposer is None:
                if self.settings.mode == "stub":
                    decomposition_backend = FixtureDecompositionBackend(
                        request.query, request.context.model_dump(),
                        [entry.model_dump() for entry in request.history],
                    )
                else:
                    decomposition_backend = LazyModelBackend("decomposition")
                    owned.append(decomposition_backend)
                decomposer = QueryDecomposer(backend=decomposition_backend)

            backend = self.backend
            if backend is None:
                if self.settings.mode == "stub":
                    backend = FixtureGenerationBackend()
                else:
                    backend = LazyModelBackend("generation")
                    owned.append(backend)
            caller = self.caller
            if caller is None:
                caller = GenerationStubToolCaller() if self.settings.mode == "stub" else McpToolCaller()
            # 스텁 모드와 생성 백엔드를 바꿔 끼운 경우(테스트)는 모델 검토자를 쓰지 않는다 (규칙 검토만)
            critic: Critic | bool = False
            if self.settings.mode != "stub" and self.backend is None and critic_enabled():
                critic = Critic()
                owned.append(critic)
            graph = build_graph(decomposer=decomposer, caller=SafeToolCaller(caller),
                                generator=SafeGenerator(AnswerGenerator(backend=backend)), critic=critic)
            with timing.collect() as timings:
                state = await graph.ainvoke({
                    "query": request.query,
                    "history": [entry.model_dump() for entry in request.history],
                    "context": request.context.model_dump(),
                }, config=tracing.run_config(str(request.request_id), self.settings.mode))
            # 단계별 시간과 호출 수. 질문 원문은 남기지 않는다
            log.info("request %s timings %s", request.request_id, timings.summary())
            decomposition = state["decomposition"]
            routing = public_routing(state.get("routing") or RouteResult(status="not_routed"))
            answer = state["answer"]
            error = None
            if answer.kind == "failed":
                status, generation_status = "FAILED", "FAILED"
                decomposition_failed = decomposition.status == "failed"
                error = ErrorDetail(
                    code="DECOMPOSITION_FAILED" if decomposition_failed else "GENERATION_FAILED",
                    message="Query decomposition failed." if decomposition_failed else "Answer generation failed.",
                    retryable=False,
                )
            else:
                status = "NEEDS_INPUT" if answer.kind == "clarify" else "COMPLETED"
                generation_status = "COMPLETED"
            return GenerationExecutionResponse(
                request_id=request.request_id, mode=self.settings.mode, execution_status=status,
                generation_status=generation_status, context=request.context,
                decomposition=decomposition, routing=routing, answer=answer, error=error,
            )
        finally:
            await asyncio.gather(*(backend.aclose() for backend in owned))
