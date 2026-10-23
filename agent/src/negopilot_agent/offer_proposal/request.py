"""협상안 요청: 회사와 조건. 나중에 엑셀 한 행으로도 받을 수 있게 조건은 평평한 필드로 둔다."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from negopilot_agent.api.review_api import ReviewCompanyFacts



class SenderInfo(BaseModel):
    """발신 담당자. 로그인한 회원(Member: name, email, department, job_title)에서 채운다.

    백엔드 프록시는 이 값을 클라이언트 입력이 아니라 인증된 세션의 회원 정보로 채워야 한다(다른 사람 이름으로 보내지 않게).
    회원 정보에는 전화번호가 없어 phone은 선택이다.
    """
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=100)
    department: str = Field(min_length=1, max_length=100)
    job_title: str = Field(default='', max_length=100)
    email: str = Field(min_length=3, max_length=254, pattern=r'^[^@\s]+@[^@\s]+$')
    phone: str | None = Field(default=None, max_length=50)


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
    # 발신 담당자. 백엔드 프록시가 로그인 세션의 회원 정보로 채운다(클라이언트 입력을 그대로 쓰지 않는다)
    sender: SenderInfo | None = None

    @model_validator(mode='after')
    def max_not_below_target(self):
        if self.max_price is not None and self.target_price is not None and self.max_price < self.target_price:
            raise ValueError('max_price는 target_price보다 작을 수 없음')
        return self
