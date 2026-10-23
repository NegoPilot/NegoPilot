"""확정한 협력사에게 제시할 협상안 하나를 만들고 check_offer로 검사한다 (#231).

숫자는 모두 이 파일의 코드가 요청 조건과 business-mcp 기준값(get_price_benchmarks)에서 계산한다. 모델은 쓰지 않는다.
1. 요청 조건을 먼저 쓰고, 빈 조건은 RFQ·시장 견적·기존 계약 값으로 채운다(filled). 요청 조건이 법 기준을 넘으면
   고치고 그 사실을 적는다(adjustments).
2. 후보(목표가 중심, 시장가 중심, 지급 조건 양보)를 만들어 각각 check_offer로 검사한다. 위반 소지(violation)가 나오면
   위반 코드에 맞춰 조건을 고쳐 다시 검사하고(revisions), 위반이 남는 후보는 버린다.
3. 남은 후보 중 하나를 고른다: 요청한 intent → 경고가 적음 → 목표 단가에 가까움.
4. 고른 안으로 협상 범위(제시가 / 목표 합의가 / 최대 수용가), 양보 순서, 예상 쟁점과 대응을 만든다.
   목표 합의가와 최대 수용가도 check_offer로 검사해 문서의 모든 가격이 위반 소지가 없게 한다.
통과한 후보가 없으면 status='no_offer'와 이유를 돌려준다. 최종 결정과 협상은 사람이 한다.
"""

import math
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from negopilot_agent.routing.mcp_client import McpToolCaller
from negopilot_agent.legal_assessment import assess_transaction

from .request import OfferProposalRequest

Intent = Literal['target', 'market', 'payment']
LABELS = {'target': '목표가 중심', 'market': '시장가 중심', 'payment': '지급 조건 양보'}
DEFAULT_CONTRACT_DAYS = 365
CONCESSION_PAYMENT_DAYS = 30   # 지급 조건 양보안의 지급 일수 (빠른 지급을 주고 목표 단가를 요청)
PAYMENT_STEP_DAYS = 15         # 양보 순서에서 제안하는 지급 단축 폭
MAX_CONTRACT_DAYS = 730        # 양보 순서에서 제안하는 계약 기간 상한 (2년)
MAX_RECHECKS = 2               # 위반을 고친 뒤 다시 검사하는 최대 횟수
LINKAGE_VIOLATIONS = {'LINKAGE_TERMS_MISSING', 'ENERGY_LINKAGE_TERMS_MISSING'}
LinkageStatus = Literal['included', 'not_required', 'exempt', 'waived', 'needs_input']
RATIO_REQUIRED = {'code': 'RAW_MATERIAL_RATIO_REQUIRED', 'severity': 'warning', 'law_refs': [],
                  'message': '원재료 비중을 입력하면 연동 의무를 판정합니다 (지금은 연동 약정 확인 필요)'}
# 이 모듈이 붙이는 경고 → 같은 뜻의 check_offer 경고. check_offer가 이미 냈으면 붙이지 않는다(중복 경고 방지)
EQUIVALENT_WARNINGS = {'RAW_MATERIAL_RATIO_REQUIRED': {'RAW_MATERIAL_RATIO_UNKNOWN'}}
SUPPLY_WARNINGS = {'CAPACITY_SHORT': '월 필요 물량 대비 생산능력을 확인하고, 부족하면 분할 발주나 납기 조정을 제안한다',
                   'CAPABILITY_MISSING': '해당 공정·소재 생산 실적과 설비를 먼저 확인한다',
                   'CERT_MISSING': '필수 인증 취득 일정과 대체 방안을 확인한다'}


class Basis(BaseModel):
    """숫자의 근거. source는 request.* (요청 조건) 또는 get_price_benchmarks 결과의 경로이고 value는 그 값 그대로다."""
    source: str
    value: int | float | bool | None
    note: str


class Revision(BaseModel):
    code: str                  # 고치게 만든 check_offer 위반 코드
    field: str
    before: Any
    after: Any


class Filled(BaseModel):
    """요청에 없어서 데이터로 채운 값."""
    field: str
    value: int | float | bool | None
    source: str


class Candidate(BaseModel):
    assessment: dict = Field(default_factory=dict, exclude=True)
    intent: Intent
    label: str
    unit_price: int
    payment_days: int
    price_linkage: bool
    raw_material_ratio: float | None
    energy_ratio: float | None
    contract_days: int
    linkage_waived_by_agreement: bool = False
    linkage_status: LinkageStatus = 'not_required'
    rationale: list[Basis]
    adjustments: list[str] = Field(default_factory=list)
    verdict_code: str | None = None
    violations: list[dict] = Field(default_factory=list)
    warnings: list[dict] = Field(default_factory=list)
    law_refs: list[dict] = Field(default_factory=list)
    revisions: list[Revision] = Field(default_factory=list)

    def terms(self) -> tuple:
        return self.unit_price, self.payment_days, self.price_linkage, self.contract_days


class PricePoint(BaseModel):
    """협상 범위의 가격 하나와 그 가격의 check_offer 결과."""
    name: Literal['opening', 'settlement', 'walk_away']
    label: str
    unit_price: int
    vs_target_pct: float
    vs_market_pct: float | None
    vs_last_quote_pct: float | None
    verdict_code: str
    warnings: list[str]


class Objection(BaseModel):
    issue: str
    response: str


class Offer(BaseModel):
    """제시할 협상안 하나. 숫자는 모두 코드가 계산한 값이다."""
    condition_basis: list[dict] = Field(default_factory=list, exclude_if=lambda value: not value)
    assessed_alternatives: list[dict] = Field(default_factory=list, exclude_if=lambda value: not value)
    commercial_comparison: dict | None = Field(default=None, exclude_if=lambda value: value is None)
    headline: str
    basis: Intent
    label: str
    unit_price: int
    payment_days: int
    price_linkage: bool
    raw_material_ratio: float | None
    energy_ratio: float | None
    contract_days: int
    contract_amount: int
    annual_volume: int
    linkage_waived_by_agreement: bool
    linkage_status: LinkageStatus
    notes: str | None
    gap_to_target_pct: float
    gap_to_last_quote_pct: float | None
    rationale: list[Basis]
    verdict_code: str
    warnings: list[dict]
    law_refs: list[dict]
    revisions: list[Revision]
    adjustments: list[str]
    price_range: list[PricePoint]
    concessions: list[str]
    objections: list[Objection]


class Considered(BaseModel):
    """고르지 않은 후보와 그 이유."""
    basis: Intent
    label: str
    unit_price: int
    payment_days: int
    price_linkage: bool
    reason: str
    violations: list[dict] = Field(default_factory=list)


class InputNeeded(BaseModel):
    """판정을 끝내려면 사람이 넣어야 하는 값. 결과와 문서 맨 위에 보여 준다."""
    field: str
    message: str


class Proposal(BaseModel):
    review_basis: dict | None = Field(default=None, exclude_if=lambda value: value is None)
    draft_status: Literal['ready', 'needs_information', 'blocked'] | None = Field(
        default=None, exclude_if=lambda value: value is None)
    status: Literal['offer', 'no_offer']
    rfq_id: str
    part_name: str
    supplier_id: str
    supplier_name: str
    as_of: str
    target_price: int
    offer: Offer | None
    considered: list[Considered]
    filled: list[Filled]
    input_needed: list[InputNeeded] = Field(default_factory=list)
    no_offer_reasons: list[str] = Field(default_factory=list)
    benchmarks: dict
    explanation: str | None = None


ToolCaller = Callable[[str, str, dict], Awaitable[Any]]
Explainer = Callable[[Proposal], Awaitable[str]]


def ceil_won(value: float) -> int:
    # 3800 × 0.9 = 3420.0000000000005 같은 부동소수 오차로 1원 올라가지 않게 반올림한 뒤 올린다
    return math.ceil(round(value, 6))


def pct(value: int, base: int | float | None) -> float | None:
    return round((value / base - 1) * 100, 1) if base else None


class Terms(BaseModel):
    """요청과 데이터로 정한 기본 조건. 후보는 여기서 단가와 지급 일수만 바꾼다."""
    target: int
    target_basis: Basis
    max_price: int | None
    payment_days: int
    payment_basis: Basis
    contract_days: int
    raw_material_ratio: float | None
    energy_ratio: float | None
    linkage_requested: bool | None
    waived: bool
    adjustments: list[str]
    filled: list[Filled]


def resolve_terms(req: OfferProposalRequest, bench: dict) -> Terms:
    rules, supplier, rfq = bench['rules'], bench['supplier'], bench['rfq']
    limit = rules['PAYMENT_DAYS_LIMIT']
    adjustments, filled = [], []

    if req.target_price is not None:
        target = req.target_price
        target_basis = Basis(source='request.target_price', value=target, note=f'요청 목표 단가 {target:,}원')
    else:
        target = rfq['target_price']
        target_basis = Basis(source='rfq.target_price', value=target, note=f'RFQ 목표 단가 {target:,}원')
        filled.append(Filled(field='target_price', value=target, source='RFQ 목표 단가'))

    if req.payment_days is not None:
        payment = req.payment_days
        payment_basis = Basis(source='request.payment_days', value=payment, note=f'요청 지급 {payment}일')
        if payment > limit:
            adjustments.append(f'요청 조건 지급 {payment}일 → 법정 {limit}일 이내로 조정')
            payment_basis = Basis(source='request.payment_days', value=payment,
                                  note=f'요청 지급 {payment}일이 법정 기한 {limit}일을 넘어 {limit}일로 조정')
            payment = limit
    elif supplier['payment_days'] is None:
        payment = limit
        payment_basis = Basis(source='rules.PAYMENT_DAYS_LIMIT', value=limit, note=f'기존 계약이 없어 법정 지급 기한 {limit}일')
        filled.append(Filled(field='payment_days', value=limit, source='법정 지급 기한'))
    else:
        existing = supplier['payment_days']
        payment = min(existing, limit)
        note = (f'기존 계약 지급 {existing}일이 법정 기한 {limit}일을 넘어 {limit}일로 맞춤' if existing > limit
                else f'기존 계약 지급 {existing}일 유지')
        payment_basis = Basis(source='supplier.payment_days', value=existing, note=note)
        filled.append(Filled(field='payment_days', value=payment, source='기존 계약'))

    if req.contract_days is not None:
        contract_days = req.contract_days
    else:
        contract_days = DEFAULT_CONTRACT_DAYS
        filled.append(Filled(field='contract_days', value=contract_days, source='기본값 1년'))

    if req.raw_material_ratio is not None:
        ratio = req.raw_material_ratio
    else:
        ratio = supplier['raw_material_ratio']
        filled.append(Filled(field='raw_material_ratio', value=ratio,
                             source='기존 계약' if ratio is not None else '기존 계약 없음(모름)'))

    return Terms(target=target, target_basis=target_basis, max_price=req.max_price, payment_days=payment,
                 payment_basis=payment_basis, contract_days=contract_days, raw_material_ratio=ratio,
                 energy_ratio=req.energy_ratio, linkage_requested=req.price_linkage,
                 waived=req.linkage_waived_by_agreement, adjustments=adjustments, filled=filled)


def ratio_source(req_ratio: float | None) -> str:
    return 'request.raw_material_ratio' if req_ratio is not None else 'supplier.raw_material_ratio'


def linkage_terms(bench: dict, terms: Terms, unit_price: int,
                  source: str) -> tuple[bool, LinkageStatus, list[Basis], list[str]]:
    """연동 약정 여부와 상태, 근거, 요청 조건을 고친 내역.

    주요 원재료(10% 이상)이고 기간·금액 예외도 미연동 합의도 아니면 연동 약정을 넣는다. 요청이 '연동 없음'이어도 넣는다.
    원재료 비중을 모르면 연동 없음으로 두지 않고 needs_input(확인 필요)으로 표시한다. 연동 포함이 정해진 경우만 예외다.
    """
    rules = bench['rules']
    ratio, requested = terms.raw_material_ratio, terms.linkage_requested
    fallback = bool(bench['supplier']['price_linkage']) if requested is None else requested
    if ratio is None:
        if fallback:
            return True, 'included', [Basis(source=source, value=None, note='원재료 비중을 모르지만 '
                                            + ('요청대로' if requested else '기존 계약대로') + ' 연동 약정 포함')], []
        return False, 'needs_input', [Basis(source=source, value=None,
                                            note='원재료 비중을 몰라 연동 의무를 판정하지 못함: 연동 약정 확인 필요')], []
    basis = [Basis(source=source, value=ratio, note=f'원재료 비중 {ratio * 100:.0f}%')]
    status: LinkageStatus = 'included' if fallback else 'not_required'
    if ratio < rules['MAJOR_MATERIAL_RATIO']:
        basis.append(Basis(source='rules.MAJOR_MATERIAL_RATIO', value=rules['MAJOR_MATERIAL_RATIO'],
                           note='주요 원재료 기준 미만이라 연동 의무 없음'))
        return fallback, status, basis, []
    amount = round(bench['rfq']['annual_volume'] * unit_price * terms.contract_days / 365)
    if terms.contract_days <= rules['LINKAGE_EXEMPT_DAYS']:
        basis.append(Basis(source='rules.LINKAGE_EXEMPT_DAYS', value=rules['LINKAGE_EXEMPT_DAYS'],
                           note=f'거래 기간 {terms.contract_days}일로 연동 면제 기간 이내'))
        return fallback, 'included' if fallback else 'exempt', basis, []
    if amount <= rules['LINKAGE_EXEMPT_AMOUNT']:
        basis.append(Basis(source='rules.LINKAGE_EXEMPT_AMOUNT', value=rules['LINKAGE_EXEMPT_AMOUNT'],
                           note=f'하도급대금 {amount:,}원으로 연동 면제 금액 이하'))
        return fallback, 'included' if fallback else 'exempt', basis, []
    if terms.waived:
        basis.append(Basis(source='request.linkage_waived_by_agreement', value=True,
                           note='연동하지 않기로 합의함: 취지와 사유를 서면에 적어야 함'))
        return False, 'waived', basis, []
    basis.append(Basis(source='rules.MAJOR_MATERIAL_RATIO', value=rules['MAJOR_MATERIAL_RATIO'],
                       note='주요 원재료 기준 이상이고 면제 사유가 없어 연동 약정 포함'))
    adjusted = [f'요청 조건 연동 없음 → 원재료 비중 {ratio * 100:.0f}%로 법정 연동 대상이라 연동 포함'] \
        if requested is False else []
    return True, 'included', basis, adjusted


def price_floors(bench: dict) -> list[tuple[int, Basis]]:
    """check_offer가 경고하지 않는 가장 낮은 단가들 (시장가 90%, 직전 견적 5% 인하)."""
    rules, market, last = bench['rules'], bench['market'], bench['supplier']['last_price']
    floors = []
    if market['median']:
        floor = ceil_won(market['median'] * rules['MARKET_PRICE_FLOOR'])
        floors.append((floor, Basis(source='market.median', value=market['median'],
                                    note=f'시장 견적 중앙값 {market["median"]:,.0f}원({market["n"]}곳)의 '
                                         f'{rules["MARKET_PRICE_FLOOR"] * 100:.0f}% = {floor:,}원 이상')))
    if last:
        floor = ceil_won(last * (1 - rules['LAST_QUOTE_CUT']))
        floors.append((floor, Basis(source='supplier.last_price', value=last,
                                    note=f'직전 견적 {last:,}원에서 {rules["LAST_QUOTE_CUT"] * 100:.0f}% 넘게 낮추지 않는 '
                                         f'{floor:,}원 이상')))
    return floors


def capped(price: int, terms: Terms, rationale: list[Basis]) -> int:
    if terms.max_price is not None and price > terms.max_price:
        rationale.append(Basis(source='request.max_price', value=terms.max_price,
                               note=f'요청 최대 수용가 {terms.max_price:,}원을 넘지 않게 제한'))
        return terms.max_price
    return price


def build_candidates(bench: dict, terms: Terms, ratio_src: str) -> tuple[list[Candidate], list[str]]:
    """후보와, 만들지 못한 후보의 이유."""
    out, skipped = [], []

    def add(intent: Intent, unit_price: int, payment_days: int, rationale: list[Basis]):
        unit_price = capped(unit_price, terms, rationale)
        linkage, status, linkage_basis, adjusted = linkage_terms(bench, terms, unit_price, ratio_src)
        out.append(Candidate(intent=intent, label=LABELS[intent], unit_price=unit_price, payment_days=payment_days,
                             price_linkage=linkage, raw_material_ratio=terms.raw_material_ratio,
                             energy_ratio=terms.energy_ratio, contract_days=terms.contract_days,
                             linkage_waived_by_agreement=terms.waived and not linkage, linkage_status=status,
                             rationale=rationale + linkage_basis, adjustments=terms.adjustments + adjusted))

    floors = price_floors(bench)
    price = max([terms.target] + [f for f, _ in floors])
    raised = [b for f, b in floors if f > terms.target and f == price]
    add('target', price, terms.payment_days, [terms.target_basis] + raised + [terms.payment_basis])

    median = bench['market']['median']
    if median:
        add('market', round(median), terms.payment_days,
            [Basis(source='market.median', value=median,
                   note=f'시장 견적 중앙값 {median:,.0f}원({bench["market"]["n"]}곳)'), terms.payment_basis])
    else:
        skipped.append(f'{LABELS["market"]}: 같은 공정·소재 시장 견적이 없음')

    concession = min(CONCESSION_PAYMENT_DAYS, terms.payment_days)
    add('payment', terms.target, concession,
        [terms.target_basis, Basis(source='offer.payment_days', value=concession,
                                   note=f'지급을 {concession}일로 앞당기는 대신 목표 단가 요청')])
    return out, skipped


def add_warning(warnings: list[dict], warning: dict) -> list[dict]:
    """같은 뜻의 경고(같은 코드나 EQUIVALENT_WARNINGS의 check_offer 코드)가 이미 있으면 그대로 둔다."""
    same = {warning['code']} | EQUIVALENT_WARNINGS.get(warning['code'], set())
    if any(w.get('code') in same for w in warnings):
        return warnings
    return warnings + [dict(warning)]


def summarize(check: dict) -> dict:
    return {'verdict_code': check.get('verdict_code'), 'violations': check.get('violations') or [],
            'warnings': check.get('warnings') or [], 'law_refs': check.get('law_refs') or [],
            'contract_amount': (check.get('offer') or {}).get('contract_amount')}


def revise(candidate: Candidate, violations: list[dict], rules: dict) -> list[Revision] | None:
    """위반 코드마다 조건을 고친다. 고칠 수 없는 위반이 있으면 None."""
    revisions = []
    for code in dict.fromkeys(v['code'] for v in violations):
        if code == 'PAYMENT_DAYS_OVER_60' and candidate.payment_days > rules['PAYMENT_DAYS_LIMIT']:
            revisions.append(Revision(code=code, field='payment_days', before=candidate.payment_days,
                                      after=rules['PAYMENT_DAYS_LIMIT']))
            candidate.payment_days = rules['PAYMENT_DAYS_LIMIT']
        elif code in LINKAGE_VIOLATIONS and not candidate.price_linkage:
            revisions.append(Revision(code=code, field='price_linkage', before=False, after=True))
            candidate.price_linkage = True
            candidate.linkage_waived_by_agreement = False
            candidate.linkage_status = 'included'
        elif code not in LINKAGE_VIOLATIONS:
            return None
    return revisions or None


def rank_key(candidate: Candidate, target: int, intent: Intent | None) -> tuple:
    """위반 없는 후보 중: 요청한 intent → 경고 수 → 목표 단가와의 차이. 같으면 min이 앞 후보를 고른다."""
    return candidate.intent != intent, len(candidate.warnings), abs(candidate.unit_price - target)


LINKAGE_WORDS = {'included': '연동 약정 포함', 'not_required': '연동 약정 없음', 'exempt': '연동 약정 없음(면제)',
                 'waived': '미연동 합의', 'needs_input': '연동 약정 확인 필요'}


def headline(supplier_id: str, c: Candidate) -> str:
    linkage = LINKAGE_WORDS[c.linkage_status]
    return f'{supplier_id}에 단가 {c.unit_price:,}원, 지급 {c.payment_days}일, {linkage}, 거래 기간 {c.contract_days}일 제시'


def lost_reason(c: Candidate, chosen: Candidate, target: int, intent: Intent | None) -> str:
    if c.terms() == chosen.terms():
        return '선택한 안과 조건이 같음'
    if intent and chosen.intent == intent != c.intent:
        return f'요청한 기준({LABELS[intent]})이 아님'
    if len(c.warnings) > len(chosen.warnings):
        return f'확인 필요 {len(c.warnings)}건으로 선택한 안({len(chosen.warnings)}건)보다 많음'
    gap, chosen_gap = abs(c.unit_price - target), abs(chosen.unit_price - target)
    if gap > chosen_gap:
        return f'목표 단가와 차이 {gap:,}원으로 선택한 안({chosen_gap:,}원)보다 큼'
    return f'확인 필요 수와 목표 단가 차이가 같아 앞 순서({chosen.label})를 고름'


def considered(c: Candidate, reason: str) -> Considered:
    return Considered(basis=c.intent, label=c.label, unit_price=c.unit_price, payment_days=c.payment_days,
                      price_linkage=c.price_linkage, reason=reason, violations=c.violations)


def walk_away_price(bench: dict, terms: Terms, opening: int) -> tuple[int, list[Filled]]:
    """최대 수용가: 요청 값, 없으면 직전 견적·시장 중앙값 중 제시가 이상인 가장 낮은 값. 제시가보다 낮을 수 없다."""
    if terms.max_price is not None:
        return max(terms.max_price, opening), []
    refs = [(round(v), name) for v, name in ((bench['supplier']['last_price'], '직전 견적'),
                                            (bench['market']['median'], '시장 견적 중앙값')) if v]
    if not refs:
        return opening, [Filled(field='max_price', value=opening, source='비교할 견적이 없어 제시가')]
    above = sorted(r for r in refs if r[0] >= opening)
    if not above:
        return opening, [Filled(field='max_price', value=opening, source='직전 견적과 시장 중앙값이 모두 제시가보다 낮아 제시가')]
    price, name = above[0]
    note = '직전 견적과 시장 중앙값 중 낮은 값' if len(above) == len(refs) else '제시가 이상인 기준값 중 낮은 값'
    return price, [Filled(field='max_price', value=price, source=f'{name} ({note})')]


def price_point(name, label, unit_price, bench, target, result) -> PricePoint:
    return PricePoint(name=name, label=label, unit_price=unit_price, vs_target_pct=pct(unit_price, target),
                      vs_market_pct=pct(unit_price, bench['market']['median']),
                      vs_last_quote_pct=pct(unit_price, bench['supplier']['last_price']),
                      verdict_code=result['verdict_code'], warnings=[w['code'] for w in result['warnings']])


def concessions(c: Candidate, settlement: int, walk_away: int) -> list[str]:
    """양보 순서 2~4개. 앞에서부터 쓴다."""
    levers = []
    if settlement > c.unit_price:
        levers.append(f'1차 양보: 단가 +{settlement - c.unit_price:,}원 → 목표 합의가 {settlement:,}원')
    if c.payment_days > CONCESSION_PAYMENT_DAYS:
        shorter = max(CONCESSION_PAYMENT_DAYS, c.payment_days - PAYMENT_STEP_DAYS)
        levers.append(f'지급 {c.payment_days}일 → {shorter}일 단축 제안 (단가 대신 현금 흐름을 양보)')
    if c.price_linkage and c.raw_material_ratio is not None:
        levers.append(f'연동 약정 포함으로 원재료(비중 {c.raw_material_ratio * 100:.0f}%) 가격 변동 리스크 분담')
    if c.contract_days < MAX_CONTRACT_DAYS:
        longer = min(c.contract_days * 2, MAX_CONTRACT_DAYS)
        levers.append(f'계약 기간 {c.contract_days}일 → {longer}일 연장 제안 (물량 안정성 제공)')
    final = (f'최종 양보: 단가 최대 {walk_away:,}원까지 (최대 수용가, 넘으면 협상 중단)' if walk_away > settlement
             else f'추가 단가 양보 없음: 최대 수용가 {walk_away:,}원')
    if not levers:
        levers.append(f'단가·지급 조건은 유지하고 납기·물량 조건으로 협의 (제시가 {c.unit_price:,}원)')
    return levers[:3] + [final]


def objections(c: Candidate, bench: dict, target: int) -> list[Objection]:
    """협력사가 꺼낼 만한 반론과 대응. 데이터와 check_offer 경고에서만 만든다."""
    rules, market, last = bench['rules'], bench['market'], bench['supplier']['last_price']
    out = []
    if last and c.unit_price < last:
        cut = (1 - c.unit_price / last) * 100
        within = c.unit_price >= ceil_won(last * (1 - rules['LAST_QUOTE_CUT']))
        out.append(Objection(
            issue=f'직전 견적 {last:,}원보다 {cut:.1f}% 낮다',
            response=(f'목표 단가 {target:,}원 기준이며 인하 폭은 직전 견적 대비 {rules["LAST_QUOTE_CUT"] * 100:.0f}% 이내다'
                      if within else '인하 근거(원가 절감 항목, 물량 증가)를 서면으로 제시하고 협의 기록을 남긴다')))
    if market['median'] and c.unit_price < market['median']:
        share = c.unit_price / market['median'] * 100
        out.append(Objection(
            issue=f'시장 견적 중앙값 {market["median"]:,.0f}원({market["n"]}곳)보다 낮다',
            response=(f'시장 중앙값의 {share:.0f}%로 하한({rules["MARKET_PRICE_FLOOR"] * 100:.0f}%) 이상이다'
                      if c.unit_price >= ceil_won(market['median'] * rules['MARKET_PRICE_FLOOR'])
                      else '시장가 대비 낮은 이유(원가 구조, 물량)를 근거로 설명하고 협의 기록을 남긴다')))
    if c.price_linkage:
        ratio = f'원재료 비중 {c.raw_material_ratio * 100:.0f}%, ' if c.raw_material_ratio is not None else ''
        out.append(Objection(issue='원재료 가격이 오르면 손실을 떠안는다',
                             response=f'연동 약정을 넣는다: {ratio}조정 요건·기준 지표·산식을 계약서에 적는다'))
    elif c.linkage_status == 'needs_input':
        out.append(Objection(issue='원재료 가격이 오르면 손실을 떠안는다',
                             response='원재료 비중을 확인해 연동 대상이면 연동 약정을 넣겠다고 답하고, 비중 자료를 요청한다'))
    elif c.linkage_waived_by_agreement:
        out.append(Objection(issue='연동하지 않으면 원재료 상승 위험이 크다',
                             response='미연동 합의의 취지와 사유를 서면에 적고, 거래상 지위를 이용해 합의를 유도하지 않는다'))
    for w in c.warnings:
        if w['code'] in SUPPLY_WARNINGS:
            out.append(Objection(issue=w['message'], response=SUPPLY_WARNINGS[w['code']]))
    return out


class OfferProposer:
    def __init__(self, caller: ToolCaller | None = None, explainer: Explainer | None = None):
        self.caller = caller or McpToolCaller()
        self.explainer = explainer   # 선택: 모델 설명 문구. 기본은 꺼 둔다

    async def check(self, c: Candidate, req: OfferProposalRequest, supplier_id: str, unit_price: int | None = None) -> dict:
        args = {'supplier': supplier_id, 'rfq_id': req.rfq_id, 'unit_price': unit_price or c.unit_price,
                'payment_days': c.payment_days, 'price_linkage': c.price_linkage, 'contract_days': c.contract_days,
                'linkage_waived_by_agreement': c.linkage_waived_by_agreement, 'as_of': req.as_of.isoformat()}
        if c.raw_material_ratio is not None:
            args['raw_material_ratio'] = c.raw_material_ratio
        if c.energy_ratio is not None:
            args['energy_ratio'] = c.energy_ratio
        for key in ('supplier_size', 'buyer_size'):
            if getattr(req, key) is not None:
                args[key] = getattr(req, key).model_dump(mode='json')
        tool_caller = self.caller
        if req.transaction is not None:
            from negopilot_agent.api.review_api import ReviewOffer
            from negopilot_agent.api.review_caller import ReviewedToolCaller
            reviewed = ReviewOffer(**(args | {'supplier_size': args.get('supplier_size') or {},
                'buyer_size': args.get('buyer_size') or {}, 'transaction': req.transaction}))
            tool_caller = ReviewedToolCaller(reviewed, self.caller)
        result = summarize(await tool_caller('business-mcp', 'check_offer', args))
        if req.transaction is not None:
            result['assessment'] = assess_transaction(req.transaction, args, req.as_of)
            if 'reduction_justification_review' in result['assessment']['missing_fields']:
                result['violations'].append({'code': 'RETROSPECTIVE_REDUCTION_UNRESOLVED',
                    'message': '기존 발주 감액의 정당성이 확인되지 않아 인하안 생성을 보류합니다', 'law_refs': []})
            if result['assessment']['status'] == 'blocked':
                result['violations'].append({'code': 'TRANSACTION_BLOCKED',
                    'message': '거래 사실 검토에서 제안을 보류합니다', 'law_refs': []})
            if result['violations']:
                result['verdict_code'] = 'VIOLATION'
        return result

    async def propose(self, req: OfferProposalRequest) -> Proposal:
        session = getattr(self.caller, 'session', None)
        if session is None:
            return await self._propose(req)
        async with session():
            return await self._propose(req)

    async def _propose(self, req: OfferProposalRequest) -> Proposal:
        bench = await self.caller('business-mcp', 'get_price_benchmarks', {'rfq_id': req.rfq_id, 'supplier': req.supplier})
        supplier_id = bench['supplier']['supplier_id']
        terms = resolve_terms(req, bench)
        built, skipped = build_candidates(bench, terms, ratio_source(req.raw_material_ratio))
        passed: list[Candidate] = []
        amounts: dict[int, int | None] = {}
        rejected: list[Considered] = []
        for candidate in built:
            result = await self.check(candidate, req, supplier_id)
            for _ in range(MAX_RECHECKS):
                if not result['violations']:
                    break
                revisions = revise(candidate, result['violations'], bench['rules'])
                if revisions is None:
                    break
                candidate.revisions += revisions
                result = await self.check(candidate, req, supplier_id)
            candidate.verdict_code = result['verdict_code']
            candidate.violations, candidate.warnings = result['violations'], result['warnings']
            candidate.law_refs = result['law_refs']
            candidate.assessment = result.get('assessment', {})
            if candidate.linkage_status == 'needs_input':
                candidate.warnings = add_warning(candidate.warnings, RATIO_REQUIRED)
            if candidate.violations:
                rejected.append(considered(candidate, '조건을 고쳐도 위반 소지가 남음'))
                continue
            amounts[id(candidate)] = result['contract_amount']
            passed.append(candidate)

        base = dict(rfq_id=bench['rfq']['rfq_id'], part_name=bench['rfq']['part_name'], supplier_id=supplier_id,
                    supplier_name=bench['supplier']['name'], as_of=req.as_of.isoformat(), target_price=terms.target,
                    benchmarks=bench)
        if not passed:
            reasons = skipped + [f'{r.label}: ' + '; '.join(v['message'] for v in r.violations) for r in rejected]
            proposal = Proposal(status='no_offer', offer=None, considered=rejected, filled=terms.filled,
                                no_offer_reasons=reasons, **base)
        else:
            from .economics import selection_key
            chosen = min(passed, key=lambda c: selection_key(c, req, bench, terms.target))
            proposal = await self._offer(req, bench, terms, chosen, amounts[id(chosen)], passed, rejected, base)
        if req.transaction is not None:
            proposal.draft_status = ('blocked' if proposal.offer is None else
                'needs_information' if proposal.offer.warnings else chosen.assessment['status'])
        if self.explainer:
            proposal.explanation = await self.explainer(proposal)
        return proposal

    async def _offer(self, req, bench, terms, chosen, amount, passed, rejected, base) -> Proposal:
        target, supplier_id = terms.target, base['supplier_id']
        if amount is None:
            amount = round(bench['rfq']['annual_volume'] * chosen.unit_price * chosen.contract_days / 365)
        walk_away, walk_filled = walk_away_price(bench, terms, chosen.unit_price)
        settlement = round((chosen.unit_price + walk_away) / 2)
        opening_result = {'verdict_code': chosen.verdict_code, 'warnings': chosen.warnings, 'violations': []}
        points = {'opening': (chosen.unit_price, opening_result)}
        for name, price in (('settlement', settlement), ('walk_away', walk_away)):
            result = opening_result if price == chosen.unit_price else await self.check(chosen, req, supplier_id, price)
            if result['violations']:   # 단가만 바꿨으므로 생기지 않아야 한다. 생기면 그 가격은 쓰지 않는다
                price, result = chosen.unit_price, opening_result
            points[name] = (price, result)
        settlement, walk_away = points['settlement'][0], points['walk_away'][0]
        labels = {'opening': '제시가', 'settlement': '목표 합의가', 'walk_away': '최대 수용가'}
        price_range = [price_point(name, labels[name], price, bench, target, result)
                       for name, (price, result) in points.items()]
        offer = Offer(headline=headline(supplier_id, chosen), basis=chosen.intent, label=chosen.label,
                      unit_price=chosen.unit_price, payment_days=chosen.payment_days,
                      price_linkage=chosen.price_linkage, raw_material_ratio=chosen.raw_material_ratio,
                      energy_ratio=chosen.energy_ratio, contract_days=chosen.contract_days, contract_amount=amount,
                      annual_volume=bench['rfq']['annual_volume'],
                      linkage_waived_by_agreement=chosen.linkage_waived_by_agreement,
                      linkage_status=chosen.linkage_status, notes=req.notes,
                      gap_to_target_pct=pct(chosen.unit_price, target),
                      gap_to_last_quote_pct=pct(chosen.unit_price, bench['supplier']['last_price']),
                      rationale=chosen.rationale, verdict_code=chosen.verdict_code, warnings=chosen.warnings,
                      law_refs=chosen.law_refs, revisions=chosen.revisions, adjustments=chosen.adjustments,
                      price_range=price_range, concessions=concessions(chosen, settlement, walk_away),
                      objections=objections(chosen, bench, target))
        from .economics import build_comparison, selection_reason
        others = [considered(c, selection_reason(c, chosen, req, bench, target)) for c in passed if c is not chosen]
        if req.transaction is not None:
            from .traceability import attach_condition_basis
            attach_condition_basis(offer, chosen.assessment, req)
            for alternative in offer.assessed_alternatives:
                changed = chosen.model_copy(update=alternative['terms'])
                checked = await self.check(changed, req, supplier_id)
                alternative['check_result'] = checked
                alternative['assessment'] = checked['assessment']
                if checked['violations']:
                    alternative['assessment']['status'] = 'blocked'
                elif checked['warnings'] and alternative['assessment']['status'] == 'ready':
                    alternative['assessment']['status'] = 'needs_information'
            facts = req.transaction
            if facts.volume_option_quantity is not None and facts.volume_option_unit_price is not None:
                changed = chosen.model_copy(update={'unit_price': facts.volume_option_unit_price})
                scenario_req = req.model_copy(update={'transaction': facts.model_copy(update={
                    'committed_quantity': facts.volume_option_quantity,
                    'actual_contract_amount': facts.volume_option_quantity * facts.volume_option_unit_price})})
                checked = await self.check(changed, scenario_req, supplier_id)
                assessment = checked['assessment']
                if checked['violations']:
                    assessment['status'] = 'blocked'
                elif checked['warnings'] and assessment['status'] == 'ready':
                    assessment['status'] = 'needs_information'
                offer.assessed_alternatives.append({'label': '물량과 단가 변경 가정 (합의 필요)',
                    'terms': changed.model_dump(), 'quantity': facts.volume_option_quantity,
                    'assessment': assessment, 'check_result': checked, 'requires_agreement': True})
            offer.commercial_comparison = build_comparison(offer, passed, bench, req, chosen.assessment)
            offer.concessions = [a['label'] + ': ' + a['assessment']['status']
                                 for a in offer.assessed_alternatives]
        needed = [InputNeeded(field='raw_material_ratio', message=RATIO_REQUIRED['message'])] \
            if chosen.linkage_status == 'needs_input' else []
        return Proposal(status='offer', offer=offer, considered=others + rejected, filled=terms.filled + walk_filled,
                        input_needed=needed, no_offer_reasons=[], **base)


def render(proposal: Proposal) -> str:
    """사람이 읽는 요약. 숫자는 협상안 값과 근거 값을 그대로 옮긴다."""
    lines = [f'{proposal.supplier_name}({proposal.supplier_id}) · {proposal.rfq_id} {proposal.part_name} 협상안']
    o = proposal.offer
    if o is None:
        lines.append('\n제시할 수 있는 협상안이 없습니다 (모든 후보에 위반 소지가 남음).')
        lines += [f'  - {r}' for r in proposal.no_offer_reasons]
    else:
        last = f', 직전 견적 대비 {o.gap_to_last_quote_pct:+.1f}%' if o.gap_to_last_quote_pct is not None else ''
        lines += [f'\n▶ {o.headline}',
                  f'  기준: {o.label} · 하도급대금 {o.contract_amount:,}원 · 목표 단가 대비 {o.gap_to_target_pct:+.1f}%{last}',
                  f'  check_offer: {o.verdict_code}']
        lines += [f'  근거: {b.note}' for b in o.rationale]
        lines += [f'  조정: {a}' for a in o.adjustments]
        lines += [f'  고침: {r.code} → {r.field} {r.before} → {r.after}' for r in o.revisions]
        lines += [f'  확인 필요: {w["message"]}' for w in o.warnings]
        lines.append('\n협상 범위:')
        lines += [f'  {p.label} {p.unit_price:,}원 (목표 대비 {p.vs_target_pct:+.1f}%, 검사 {p.verdict_code})'
                  for p in o.price_range]
        lines.append('양보 순서:')
        lines += [f'  {n}. {text}' for n, text in enumerate(o.concessions, 1)]
        if o.objections:
            lines.append('예상 쟁점과 대응:')
            lines += [f'  - {x.issue} → {x.response}' for x in o.objections]
    if proposal.filled:
        lines.append('\n자동으로 채운 값: ' + ', '.join(f'{f.field}={f.value} ({f.source})' for f in proposal.filled))
    if proposal.considered:
        lines.append('\n검토했지만 고르지 않은 후보:')
        lines += [f'  - {c.label} (단가 {c.unit_price:,}원, 지급 {c.payment_days}일): {c.reason}'
                  for c in proposal.considered]
    if proposal.explanation:
        lines.append(f'\n{proposal.explanation}')
    return '\n'.join(lines)
