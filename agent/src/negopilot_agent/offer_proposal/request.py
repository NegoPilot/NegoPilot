"""협상안 요청: 회사와 조건. 나중에 엑셀 한 행으로도 받을 수 있게 조건은 평평한 필드로 둔다."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from negopilot_agent.api.review_api import ReviewCompanyFacts


class OfferProposalRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    supplier: str = Field(pattern=r'^SUP-\d{4}$')
    rfq_id: str = Field(pattern=r'^RFQ-\d{4}-\d{3}$')
    as_of: Annotated[date, Field(strict=False)]
    # 아래 조건은 모두 선택. 비우면 RFQ·시장 견적·기존 계약 값으로 채우고, 채운 값을 결과에 적는다
    intent: Literal['target', 'market', 'payment'] | None = None
    target_price: int | None = Field(default=None, gt=0)         # 목표 단가(원). 비우면 RFQ 목표 단가
    max_price: int | None = Field(default=None, gt=0)            # 최대 수용가(원). 비우면 min(직전 견적, 시장 중앙값)
    payment_days: int | None = Field(default=None, gt=0)
    price_linkage: bool | None = None
    raw_material_ratio: float | None = Field(default=None, ge=0, le=1)
    energy_ratio: float | None = Field(default=None, ge=0, le=1)
    contract_days: int | None = Field(default=None, gt=0)
    linkage_waived_by_agreement: bool = False
    notes: str | None = Field(default=None, max_length=500)       # 납품·비고 (문서에 그대로 옮긴다)
    supplier_size: ReviewCompanyFacts | None = None
    buyer_size: ReviewCompanyFacts | None = None

    @model_validator(mode='after')
    def max_not_below_target(self):
        if self.max_price is not None and self.target_price is not None and self.max_price < self.target_price:
            raise ValueError('max_price는 target_price보다 작을 수 없음')
        return self
