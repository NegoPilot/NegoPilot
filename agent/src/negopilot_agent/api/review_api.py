"""Structured legal review: form values never pass through language extraction."""
import asyncio
from datetime import date
from typing import Annotated

from fastapi import Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from negopilot_shared import DecompositionResult, SubQuery, Entities
from negopilot_shared.decomposition import CompanySizeFacts
from .generation_execution import GenerationExecutor
from .generation_models import GenerationExecutionRequest


class ReviewCompanyFacts(CompanySizeFacts):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    ksic_code: str | None = Field(default=None, max_length=20)
    evidence_reference: str | None = Field(default=None, max_length=500)


class ReviewOffer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    supplier: str = Field(pattern=r"^SUP-\d{4}$")
    rfq_id: str = Field(pattern=r"^RFQ-\d{4}-\d{3}$")
    unit_price: int = Field(gt=0)
    payment_days: int = Field(gt=0)
    price_linkage: bool
    raw_material_ratio: float | None = Field(default=None, ge=0, le=1)
    contract_days: int = Field(gt=0)
    linkage_waived_by_agreement: bool = False
    supplier_size: ReviewCompanyFacts
    buyer_size: ReviewCompanyFacts
    as_of: Annotated[date, Field(strict=False)]


class ReviewRequest(GenerationExecutionRequest):
    offer: ReviewOffer


class ReviewDecomposer:
    def __init__(self, offer: ReviewOffer):
        self.offer = offer

    async def decompose(self, query, history=None, *, context=None):
        questions = [
            SubQuery(id="q1", question=query, original_text=query, intent="offer_check",
                     grounding="required", confidence=1,
                     entities=Entities(**self.offer.model_dump(mode="json"))),
        ]
        for index, text in enumerate([
            "중소기업기본법 시행령 별표 1 업종별 평균매출액 중소기업 규모 기준",
            "중소기업기본법 시행령 별표 3 소기업 업종별 평균매출액 기준",
            "하도급대금 연동 소기업 단기 소액 미연동 합의 예외",
        ], 2):
            questions.append(SubQuery(id=f"q{index}", question=text, original_text=text,
                intent="law_check", grounding="required", confidence=1,
                entities=Entities(as_of=self.offer.as_of.isoformat())))
        return DecompositionResult(status="ok", original_query=query, sub_queries=questions)


def add_review_routes(app, settings, authenticate, error_response, caller=None):
    @app.post("/internal/v2/legal-reviews", dependencies=[Depends(authenticate)])
    async def review(body: ReviewRequest):
        try:
            async with asyncio.timeout(settings.request_timeout_seconds):
                executor = GenerationExecutor(settings, decomposer=ReviewDecomposer(body.offer), caller=caller)
                result = await executor.execute(GenerationExecutionRequest(
                    request_id=body.request_id, query=body.query, context=body.context))
                return JSONResponse(result.model_dump(mode="json", by_alias=True))
        except TimeoutError:
            return error_response(504, body.request_id, "UPSTREAM_TIMEOUT", "Execution timed out.", True)
        except Exception:
            return error_response(500, body.request_id, "INTERNAL_ERROR", "Unexpected server error.")
