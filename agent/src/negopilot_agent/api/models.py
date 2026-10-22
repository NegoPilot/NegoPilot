"""Request and error envelopes shared by the internal HTTP routes."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

NonBlankText = Annotated[str, Field(strict=True, min_length=1, max_length=4000)]


class HistoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: NonBlankText

    @field_validator("content")
    @classmethod
    def content_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class QueryExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    query: NonBlankText
    history: list[HistoryEntry] = Field(default_factory=list, max_length=20)

    @field_validator("query")
    @classmethod
    def query_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool


class ErrorResponse(BaseModel):
    request_id: UUID | None
    error: ErrorDetail

