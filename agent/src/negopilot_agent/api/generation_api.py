"""Authenticated v2 endpoint for complete Agent generation."""

import asyncio
from collections.abc import Callable

import anthropic
import httpx2
import openai
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from negopilot_agent.decomposition.backends import Backend
from negopilot_agent.routing import ToolCaller

from .execution import ModelUnavailable
from .generation_execution import ContextDecomposer, GenerationExecutor
from .generation_models import GenerationExecutionRequest, GenerationExecutionResponse
from .models import ErrorResponse
from .settings import ApiSettings
from .stubs import UnsupportedStubQuery


def add_generation_routes(application: FastAPI, settings: ApiSettings, authenticate: Callable,
                          error_response: Callable, decomposer: ContextDecomposer | None = None,
                          caller: ToolCaller | None = None, backend: Backend | None = None) -> None:
    executor = GenerationExecutor(settings, decomposer=decomposer, caller=caller, backend=backend)

    @application.post(
        "/internal/v2/query-executions", response_model=GenerationExecutionResponse,
        dependencies=[Depends(authenticate)],
        responses={status: {"model": ErrorResponse} for status in (400, 401, 500, 503, 504)},
    )
    async def execute(body: GenerationExecutionRequest) -> JSONResponse:
        try:
            async with asyncio.timeout(settings.request_timeout_seconds):
                result = await executor.execute(body)
                return JSONResponse(content=result.model_dump(mode="json", by_alias=True))
        except UnsupportedStubQuery:
            return error_response(400, body.request_id, "VALIDATION_ERROR", "Unsupported stub query. See agent/README.md.")
        except (TimeoutError, httpx2.TimeoutException, anthropic.APITimeoutError, openai.APITimeoutError):
            return error_response(504, body.request_id, "UPSTREAM_TIMEOUT", "Execution timed out.", True)
        except (ModelUnavailable, httpx2.HTTPError, httpx2.InvalidURL, anthropic.APIError, openai.OpenAIError):
            return error_response(503, body.request_id, "UPSTREAM_UNAVAILABLE", "Model service is unavailable.", True)
        except Exception:
            return error_response(500, body.request_id, "INTERNAL_ERROR", "Unexpected server error.")
