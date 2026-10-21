"""The v0.3 HTTP envelope around the complete Agent graph."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from negopilot_shared import DecompositionResult, RouteResult
from negopilot_agent.generation import Answer

from .models import ErrorDetail, QueryExecutionRequest
from .supplier_chat_models import Context


class GenerationExecutionRequest(QueryExecutionRequest):
    context: Context = Field(default_factory=lambda: Context(selected_supplier_ids=[]))


class GenerationExecutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.3"] = "0.3"
    request_id: UUID
    mode: Literal["live", "stub"]
    execution_status: Literal["COMPLETED", "NEEDS_INPUT", "FAILED"]
    generation_status: Literal["COMPLETED", "FAILED"]
    context: Context
    decomposition: DecompositionResult
    routing: RouteResult
    answer: Answer
    error: ErrorDetail | None = None

    @field_validator("answer")
    @classmethod
    def answer_text_is_not_blank(cls, value: Answer) -> Answer:
        if not value.text.strip():
            raise ValueError("Answer text must not be blank")
        return value
