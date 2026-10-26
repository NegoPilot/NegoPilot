"""Structured legal review: form values never pass through language extraction."""
import asyncio
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from negopilot_shared import DecompositionResult, SubQuery, Entities
from negopilot_shared.decomposition import CompanySizeFacts
from .generation_execution import GenerationExecutor
from .generation_models import GenerationExecutionRequest
from negopilot_shared.transaction import TransactionFacts
from negopilot_agent.legal_assessment import assess_transaction
from .review_snapshot import sign_review
from .review_caller import ReviewedToolCaller
from .generation_stubs import GenerationStubToolCaller
from negopilot_agent.routing.mcp_client import McpToolCaller


class ReviewCompanyFacts(CompanySizeFacts):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    ksic_code: str | None = Field(default=None, max_length=20)
    evidence_reference: str | None = Field(default=None, max_length=500)


class ReviewOffer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    transaction: TransactionFacts | None = None
    supplier: str = Field(pattern=r"^SUP-\d{4}$")
    rfq_id: str = Field(pattern=r"^RFQ-\d{4}-\d{3}$")
    unit_price: int = Field(gt=0)
    payment_days: int = Field(gt=0)
    price_linkage: bool
    raw_material_ratio: float | None = Field(default=None, ge=0, le=1)
    energy_ratio: float | None = Field(default=None, ge=0, le=1)
    contract_days: int = Field(gt=0)
    linkage_waived_by_agreement: bool = False
    supplier_size: ReviewCompanyFacts
    buyer_size: ReviewCompanyFacts
    as_of: Annotated[date, Field(strict=False)]


class ReviewRequest(GenerationExecutionRequest):
    offer: ReviewOffer


FILE_REVIEW_TIMEOUT_SECONDS = 240


class FileReviewRequest(BaseModel):
    """엑셀 파일 하나의 행 전체. 행 번호는 offers 순서(1부터)다."""
    model_config = ConfigDict(extra="forbid")
    request_id: UUID | None = None
    offers: list[ReviewOffer] = Field(min_length=1, max_length=100)


class ReviewDecomposer:
    def __init__(self, offer: ReviewOffer):
        self.offer = offer

    async def decompose(self, query, history=None, *, context=None):
        questions = [
            SubQuery(id="q1", question=query, original_text=query, intent="offer_check",
                     grounding="required", confidence=1,
                     entities=Entities(**self.offer.model_dump(mode="json", exclude={"transaction"}))),
        ]
        for index, text in enumerate([
            "중소기업기본법 시행령 별표 1 업종별 평균매출액 중소기업 규모 기준",
            "중소기업기본법 시행령 별표 3 소기업 업종별 평균매출액 기준",
            "하도급대금 연동 소기업 단기 소액 미연동 합의 예외",
        ], 2):
            questions.append(SubQuery(id=f"q{index}", question=text, original_text=text,
                intent="law_check", grounding="required", confidence=1,
                entities=Entities(as_of=self.offer.as_of.isoformat())))
        if self.offer.transaction is not None:
            for text in ["하도급법 단가 결정 감액 제4조 제11조 지급기일 제13조 위탁취소 제8조 심사지침"]:
                questions.append(SubQuery(id=f"q{len(questions)+1}", question=text, original_text=text,
                    intent="law_check", grounding="required", confidence=1,
                    entities=Entities(as_of=self.offer.as_of.isoformat())))
        return DecompositionResult(status="ok", original_query=query, sub_queries=questions)


def add_review_routes(app, settings, authenticate, error_response, caller=None, file_review_client=None):
    @app.post("/internal/legal-reviews/file", dependencies=[Depends(authenticate)])
    async def review_file(body: FileReviewRequest):
        """파일 단위 검토 에이전트 (negopilot_agent.file_review). 행 판정은 규칙 결과 그대로다."""
        from negopilot_agent.file_review import FileReviewAgent
        try:
            # 모델이 도구를 여러 번 부르므로 행 검토(request_timeout_seconds)보다 길게 둔다
            async with asyncio.timeout(max(settings.request_timeout_seconds, FILE_REVIEW_TIMEOUT_SECONDS)):
                actual_caller = caller or (GenerationStubToolCaller() if settings.mode == "stub" else McpToolCaller())
                offers = [o.model_dump(mode="json") for o in body.offers]
                by_row = {id(o): ReviewedToolCaller(m, actual_caller) for o, m in zip(offers, body.offers)}
                agent = FileReviewAgent(actual_caller, client=file_review_client)
                session = getattr(actual_caller, "session", None)
                if session:
                    async with session():
                        result = await agent.run(offers, lambda o: by_row[id(o)])
                else:
                    result = await agent.run(offers, lambda o: by_row[id(o)])
                return JSONResponse({"request_id": str(body.request_id) if body.request_id else None, **result})
        except TimeoutError:
            return error_response(504, body.request_id, "UPSTREAM_TIMEOUT", "Execution timed out.", True)
        except Exception:
            return error_response(500, body.request_id, "INTERNAL_ERROR", "Unexpected server error.")

    @app.post("/internal/legal-reviews", dependencies=[Depends(authenticate)])
    async def review(body: ReviewRequest):
        try:
            async with asyncio.timeout(settings.request_timeout_seconds):
                actual_caller = caller or (GenerationStubToolCaller() if settings.mode == "stub" else None)
                reviewed = ReviewedToolCaller(body.offer, actual_caller)
                executor = GenerationExecutor(settings, decomposer=ReviewDecomposer(body.offer), caller=reviewed,
                                              issue_review=False)   # 08-2는 고정 하위 질문과 룰 판정을 그대로 쓴다
                result = await executor.execute(GenerationExecutionRequest(
                    request_id=body.request_id, query=body.query, context=body.context))
                content = result.model_dump(mode="json", by_alias=True)
                offer = body.offer.model_dump(mode="json")
                assessment = assess_transaction(body.offer.transaction, offer, body.offer.as_of,
                                                await reviewed.law_version())
                complete = all(outcome.get("status") == "ok" for outcome in content["routing"]["outcomes"])
                assessment["sources_complete"] = complete
                if not complete:
                    assessment["missing_fields"].append("official_source_retrieval")
                    if assessment["status"] == "ready":
                        assessment["status"] = "needs_information"
                # A failed retrieval/generation must never become a reusable completed review.
                if result.execution_status != "FAILED":
                    evidence = [{k: e.get(k) for k in ("id", "label", "source_url", "meta")}
                                for e in content["answer"]["evidence"] if e.get("source") == "law"]
                    content["legal_review"] = sign_review(offer, assessment, evidence, settings.internal_api_key)
                    content["answer"]["notices"].append(
                        "거래 사실 기반 판단은 아래 구조화된 검토 결과를 확인하세요. 미확인 항목이 있는 협상안은 조건부 초안입니다.")
                return JSONResponse(content)
        except TimeoutError:
            return error_response(504, body.request_id, "UPSTREAM_TIMEOUT", "Execution timed out.", True)
        except Exception:
            return error_response(500, body.request_id, "INTERNAL_ERROR", "Unexpected server error.")
