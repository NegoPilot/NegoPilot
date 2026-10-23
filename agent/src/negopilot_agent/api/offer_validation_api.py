"""저장된 외부 문구와 협상 조건 및 가격별 검사."""

import asyncio
import json
import re
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator

from negopilot_agent.generation.verify import known_numbers
from negopilot_agent.offer_proposal.document import OfferDocument
from negopilot_agent.offer_proposal.generator import Candidate, OfferProposer, pct
from negopilot_agent.offer_proposal.request import OfferProposalRequest
from negopilot_agent.offer_proposal.writer import INTERNAL_TERMS, supplier_terms, unknown_numbers
from negopilot_agent.routing.mcp_client import McpToolCaller

from .offer_api import INPUT_ERRORS
from .review_api import ReviewOffer

VALIDATION_TOOL_ERRORS = INPUT_ERRORS | {'DB_UNAVAILABLE', 'SUPPLIER_AMBIGUOUS'}


class TextCheckBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    document: OfferDocument
    texts: dict[str, Annotated[str, Field(max_length=2000)]] = Field(max_length=20)

    @field_validator('document', mode='before')
    @classmethod
    def ignore_hash(cls, value):
        if isinstance(value, dict):
            return {key: item for key, item in value.items() if key != 'content_hash'}
        return value


class ValidationPricePoint(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1)
    unit_price: int = Field(gt=0)


class OfferValidationBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    offer: ReviewOffer
    price_points: list[ValidationPricePoint] = Field(default_factory=list)


@asynccontextmanager
async def caller_session(caller):
    session = getattr(caller, 'session', None)
    if session is None:
        yield
    else:
        async with session():
            yield


async def validate_conditions(body: OfferValidationBody, caller) -> dict:
    offer = body.offer
    req = OfferProposalRequest.model_validate(offer.model_dump(exclude={'unit_price'}))
    candidate = Candidate(intent='target', label='', rationale=[], **{
        key: getattr(offer, key) for key in ('unit_price', 'payment_days', 'price_linkage',
                                            'contract_days', 'raw_material_ratio', 'energy_ratio',
                                            'linkage_waived_by_agreement')})
    proposer = OfferProposer(caller)
    async with caller_session(caller):
        conditions = await proposer.check(candidate, req, offer.supplier)
        points = []
        for point in body.price_points:
            checked = await proposer.check(candidate, req, offer.supplier, point.unit_price)
            points.append(point.model_dump() | checked)
        benchmarks = await caller('business-mcp', 'get_price_benchmarks',
                                  {'rfq_id': offer.rfq_id, 'supplier': offer.supplier})
    volume = benchmarks['rfq']['annual_volume']
    for price, result in [(offer.unit_price, conditions), *[(p['unit_price'], p) for p in points]]:
        if result['contract_amount'] is None:
            result['contract_amount'] = round(volume * price * offer.contract_days / 365)
        result.update(vs_target_pct=pct(price, benchmarks['rfq']['target_price']),
                      vs_market_pct=pct(price, benchmarks['market']['median']),
                      vs_last_quote_pct=pct(price, benchmarks['supplier']['last_price']))
    return {'request_id': str(body.request_id), 'conditions': conditions, 'price_points': points,
            'scope': ['선택한 협상안의 조건 검사', '협상 범위의 가격별 검사'],
            'limitations': ['양보 순서의 모든 조건 조합을 검사하지는 않았습니다']}


def add_offer_validation_routes(app, settings, authenticate, error_response, caller=None):
    @app.post('/internal/offer-texts/check', dependencies=[Depends(authenticate)])
    async def check_texts(body: TextCheckBody):
        public = supplier_terms(body.document)
        public.update({key: getattr(body.document, key).isoformat() for key in ('issued_on', 'as_of')})
        allowed = known_numbers([json.dumps(public, ensure_ascii=False)])
        findings = []
        for field, text in body.texts.items():
            if not text:
                continue
            findings.extend({'field': field, 'code': 'INTERNAL_TERM', 'detail': term}
                            for term in INTERNAL_TERMS if term in text)
            findings.extend({'field': field, 'code': 'UNKNOWN_NUMBER', 'detail': number}
                            for number in unknown_numbers(text, allowed))
        return {'request_id': str(body.request_id), 'checked': True, 'findings': findings,
                'method': ['내부 협상 용어 목록 대조', '숫자는 제안서 공개 조건 값과 대조'],
                'limitations': ['목록에 없는 표현이나 한글로 쓴 숫자는 탐지하지 못합니다']}

    @app.post('/internal/offer-proposals/validate', dependencies=[Depends(authenticate)])
    async def validate(body: OfferValidationBody):
        try:
            async with asyncio.timeout(settings.request_timeout_seconds):
                return await validate_conditions(body, caller or McpToolCaller())
        except TimeoutError:
            return error_response(504, body.request_id, 'UPSTREAM_TIMEOUT', '검사 시간이 초과되었습니다.', True)
        except Exception as exc:
            code = re.match(r'\[([A-Z_]+)\]', str(exc))
            if code and code[1] in VALIDATION_TOOL_ERRORS:
                return error_response(422, body.request_id, code[1], '제안 조건을 확인하세요.')
            return error_response(500, body.request_id, 'INTERNAL_ERROR', '검사 중 오류가 발생했습니다.')
