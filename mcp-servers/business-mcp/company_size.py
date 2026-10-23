"""Reviewed, source-versioned manufacturing size checks. Never infer KSIC from a process."""
import json
from datetime import date
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field

RULES = json.loads((Path(__file__).parent/'data/legal_size_rules.json').read_text())

class CompanySizeInput(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    ksic_code: str | None = None
    average_revenue_eok: float | None = Field(default=None, ge=0, description="평균매출액, 억원 단위. 1100억원은 1100; 원으로 환산하지 않음.")
    assets_eok: float | None = Field(default=None, ge=0, description="자산총액, 억원 단위. 2000억원은 2000; 원으로 환산하지 않음.")
    independence_met: bool | None = None
    has_related_enterprises: bool | None = None
    related_aggregate_revenue_eok: float | None = Field(default=None, ge=0, description="검증된 관계기업 합산 평균매출액, 억원 단위.")
    related_calculation_verified: bool = False
    # Seat exceptions: C30/C31 parent groups and C31202/C31322 subclasses.
    seat_manufacturing: bool | None = None
    transition_reviewed: bool = False
    # Must be based on a valid certificate/legal review; never inferred from turnover.
    deemed_sme: bool | None = None
    evidence_reference: str | None = None


def assess(inputs: CompanySizeInput | dict | None, as_of: str | None = None) -> dict:
    v = inputs if isinstance(inputs, CompanySizeInput) else CompanySizeInput.model_validate(inputs or {})
    day = date.fromisoformat(as_of).isoformat() if as_of else date.today().isoformat()
    result = {'status':'unknown', 'is_sme':None, 'is_small_business':None, 'as_of':day,
              'basis':'중소기업기본법 시행령 별표 1·2·3 및 본문 요건',
              'source_url':RULES['parent_source_url'], 'source_documents':RULES['sources'],
              'reviewed_as_of':RULES['reviewed_as_of'], 'missing':[],
              'note':'기업 규모 판정이며, 하도급법 적용은 위탁유형·원사업자 요건 등 별도 확인 필요.'}
    if not RULES['coverage_from'] <= day <= RULES['reviewed_as_of']:
        result['missing']=['기준일에 검증된 별표 판본']; return result
    code=(v.ksic_code or '').strip().upper()
    # Require complete manufacturing middle group or exact subclass; never accept arbitrary prefix junk.
    import re
    if not re.fullmatch(r'C\d{2}(?:\d{3})?',code) or code[:3] not in RULES['thresholds']:
        result['missing']=['지원되는 제조업 주된 업종 KSIC 코드(C10~C34)']; return result
    thresholds=dict(RULES['thresholds'][code[:3]])
    if code=='C30393': thresholds=RULES['exceptions'][code]
    elif code=='C30' and v.seat_manufacturing is not False:
        result['missing'].append('자동차용 신품 의자(C30393) 해당 여부 확인을 위한 세부 업종코드')
    if code in ('C31','C31202','C31322'):
        if v.seat_manufacturing is None: result['missing'].append('철도·항공기용 의자 제조 여부')
        elif v.seat_manufacturing:
            if code=='C31': result['missing'].append('의자 제조 세부 업종코드(C31202/C31322)')
            else: thresholds=RULES['exceptions'][code+'_seat']
    result['ksic_code']=code; result['thresholds']=thresholds
    for field in ('average_revenue_eok','assets_eok','independence_met','has_related_enterprises'):
        if getattr(v,field) is None: result['missing'].append(field)
    if v.has_related_enterprises and (v.related_aggregate_revenue_eok is None or not v.related_calculation_verified):
        result['missing'].append('별표 2에 따라 검증된 관계기업 평균매출액등')
    if not v.transition_reviewed: result['missing'].append('중소기업 유예·경과조치 검토')
    if not (v.evidence_reference or '').strip(): result['missing'].append('기업 규모 입력 근거')
    # Expose the revenue comparison even when overall eligibility is unresolved.
    result['revenue_criterion_met'] = (v.average_revenue_eok <= thresholds['sme_eok']) if v.average_revenue_eok is not None else None
    if result['missing']: return result
    meets=(v.average_revenue_eok<=thresholds['sme_eok'] and v.assets_eok<RULES['asset_limit_exclusive_eok']
           and v.independence_met and (not v.has_related_enterprises or v.related_aggregate_revenue_eok<=thresholds['sme_eok']))
    # Passing ordinary criteria doesn't require a grace-period override.
    if not meets and v.deemed_sme is None:
        result['missing']=['기준 초과 시 중소기업 간주·유예 해당 여부']; return result
    result['is_sme']=bool(meets or v.deemed_sme)
    # Grace-period SME status alone does not establish small-business status.
    result['is_small_business']=(v.average_revenue_eok<=thresholds['small_eok']) if meets else (False if not v.deemed_sme else None)
    result['status']='criteria_met' if result['is_sme'] else 'criteria_not_met'
    result['evidence_reference']=v.evidence_reference
    return result
