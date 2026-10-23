"""협상안 하나 고르기, 요청 조건 반영, 협상 범위·양보 순서·쟁점, check_offer 위반 수정, 자료 누락 처리 (#231).

실제 MCP 서버 없이 가짜 caller로 검사한다.
"""

import asyncio
import copy
from contextlib import asynccontextmanager

import pytest
from pydantic import ValidationError

from negopilot_agent.offer_proposal import OfferProposalRequest, OfferProposer, render
from negopilot_agent.offer_proposal.generator import Candidate, ceil_won, revise

RULES = {'PAYMENT_DAYS_LIMIT': 60, 'MAJOR_MATERIAL_RATIO': 0.10, 'LINKAGE_EXEMPT_DAYS': 90,
         'LINKAGE_EXEMPT_AMOUNT': 100_000_000, 'MARKET_PRICE_FLOOR': 0.90, 'LAST_QUOTE_CUT': 0.05}


def bench(**over):
    out = {'rfq': {'rfq_id': 'RFQ-2026-001', 'part_name': '브래킷', 'process': '프레스', 'material': 'SPCC',
                   'annual_volume': 120_000, 'target_price': 3800, 'required_cert': None},
           'market': {'median': 4000.0, 'n': 12, 'min': 3500, 'max': 4600},
           'supplier': {'supplier_id': 'SUP-0007', 'name': '가상정밀', 'last_price': 4100, 'payment_days': 45,
                        'price_linkage': False, 'raw_material_ratio': 0.4, 'years_dealing': 3},
           'rules': dict(RULES)}
    for path, value in over.items():
        section, key = path.split('__')
        out[section][key] = value
    return out


class FakeCaller:
    """get_price_benchmarks는 정해 둔 값을, check_offer는 단순화한 규칙 결과를 돌려준다."""

    def __init__(self, benchmarks, check=None):
        self.benchmarks = benchmarks
        self.check = check or self.default_check
        self.calls = []
        self.sessions = 0

    @asynccontextmanager
    async def session(self):
        self.sessions += 1
        yield

    async def __call__(self, server, tool, args):
        self.calls.append((server, tool, copy.deepcopy(args)))
        if tool == 'get_price_benchmarks':
            return copy.deepcopy(self.benchmarks)
        return self.check(args)

    def default_check(self, args):
        violations, warnings = [], []
        if args['payment_days'] > 60:
            violations.append({'code': 'PAYMENT_DAYS_OVER_60', 'severity': 'violation', 'message': '지급 초과'})
        median = self.benchmarks['market']['median']
        if median and args['unit_price'] < median * 0.9:
            warnings.append({'code': 'PRICE_BELOW_MARKET', 'severity': 'warning', 'message': '시장가 대비 낮음'})
        code = 'VIOLATION' if violations else 'CHECK_NEEDED' if warnings else 'OK'
        return {'verdict_code': code, 'violations': violations, 'warnings': warnings, 'info': [],
                'law_refs': [{'law': 'subcontract_act', 'article_no': '제4조', 'paragraph': '①'}] if warnings else []}


def request(**kw):
    return OfferProposalRequest.model_validate({'supplier': 'SUP-0007', 'rfq_id': 'RFQ-2026-001',
                                                'as_of': '2026-10-23', **kw}, strict=False)


def propose(caller, **kw):
    return asyncio.run(OfferProposer(caller).propose(request(**kw)))


def checks(caller):
    return [args for _, tool, args in caller.calls if tool == 'check_offer']


def considered(out, basis):
    return next(c for c in out.considered if c.basis == basis)


def test_one_offer_with_exact_sources_and_gaps():
    caller = FakeCaller(bench(market__median=4300.0))
    out = propose(caller)
    assert out.status == 'offer' and out.no_offer_reasons == []
    o = out.offer
    # 목표 3800, 시장 90% = 3870, 직전 견적 4100의 95% = 3895 → 3895 (지급 양보안 3800은 시장가 경고로 밀림)
    assert o.basis == 'target' and o.unit_price == max(3800, ceil_won(4300 * 0.9), ceil_won(4100 * 0.95)) == 3895
    assert o.headline == 'SUP-0007에 단가 3,895원, 지급 45일, 연동 약정 포함, 거래 기간 365일 제시'
    sources = {b.source: b.value for b in o.rationale}
    assert sources['rfq.target_price'] == 3800 and sources['supplier.last_price'] == 4100
    assert 'market.median' not in sources     # 단가를 정한 하한만 근거로 단다
    assert sources['supplier.payment_days'] == 45 and o.payment_days == 45
    assert o.price_linkage is True            # 원재료 40%, 면제 사유 없음
    assert o.contract_amount == round(120_000 * 3895)
    assert o.gap_to_target_pct == 2.5 and o.gap_to_last_quote_pct == -5.0
    assert o.verdict_code == 'OK' and o.warnings == []
    assert considered(out, 'payment').reason.startswith('확인 필요 1건')
    assert considered(out, 'market').reason.startswith('목표 단가와 차이')
    assert caller.sessions == 1


def test_negotiation_range_is_checked_and_ordered():
    caller = FakeCaller(bench(market__median=4300.0))
    o = propose(caller).offer
    # 최대 수용가: 직전 견적 4100과 시장 중앙값 4300 중 제시가 이상인 낮은 값 → 4100, 목표 합의가는 가운데
    assert [(p.name, p.unit_price) for p in o.price_range] == [('opening', 3895), ('settlement', 3998),
                                                               ('walk_away', 4100)]
    assert all(p.verdict_code != 'VIOLATION' for p in o.price_range)
    assert sorted(a['unit_price'] for a in checks(caller)[-2:]) == [3998, 4100]   # 합의가와 수용가도 검사
    walk = o.price_range[2]
    assert (walk.vs_target_pct, walk.vs_market_pct, walk.vs_last_quote_pct) == (7.9, -4.7, 0.0)
    assert o.concessions[0] == '1차 양보: 단가 +103원 → 목표 합의가 3,998원'
    assert o.concessions[-1] == '최종 양보: 단가 최대 4,100원까지 (최대 수용가, 넘으면 협상 중단)'
    assert 2 <= len(o.concessions) <= 4


def test_walk_away_uses_request_and_never_goes_below_opening():
    o = propose(FakeCaller(bench(market__median=4300.0)), max_price=4200).offer
    assert o.price_range[2].unit_price == 4200
    low = propose(FakeCaller(bench(market__median=4300.0)), max_price=3500)
    # 최대 수용가가 목표가 아래면 제시가도 그 값으로 제한되고, 수용가는 제시가 아래로 내려가지 않는다
    assert low.offer.unit_price == 3500 and low.offer.price_range[2].unit_price == 3500
    assert any(b.source == 'request.max_price' for b in low.offer.rationale)


def test_request_conditions_override_and_illegal_ones_are_adjusted():
    out = propose(FakeCaller(bench()), payment_days=90, price_linkage=False, target_price=3900,
                  contract_days=180, notes='월 2회 분할 납품')
    o = out.offer
    assert o.payment_days == 60 and o.price_linkage is True and o.contract_days == 180
    assert o.adjustments == ['요청 조건 지급 90일 → 법정 60일 이내로 조정',
                             '요청 조건 연동 없음 → 원재료 비중 40%로 법정 연동 대상이라 연동 포함']
    assert {b.source: b.value for b in o.rationale}['request.target_price'] == 3900
    assert o.notes == '월 2회 분할 납품'
    assert {f.field for f in out.filled} == {'raw_material_ratio', 'max_price'}


def test_missing_conditions_are_filled_and_listed():
    out = propose(FakeCaller(bench(market__median=4300.0)))
    filled = {f.field: (f.value, f.source) for f in out.filled}
    assert filled['target_price'] == (3800, 'RFQ 목표 단가')
    assert filled['payment_days'] == (45, '기존 계약')
    assert filled['contract_days'] == (365, '기본값 1년')
    assert filled['raw_material_ratio'] == (0.4, '기존 계약')
    assert filled['max_price'][0] == 4100


def test_waived_linkage_is_kept_with_written_reason():
    o = propose(FakeCaller(bench()), linkage_waived_by_agreement=True).offer
    assert o.price_linkage is False and o.linkage_waived_by_agreement is True
    assert any('미연동 합의' in x.response for x in o.objections)


def test_objections_come_from_data_and_warnings():
    def check(args):
        return {'verdict_code': 'CHECK_NEEDED', 'violations': [], 'law_refs': [],
                'warnings': [{'code': 'CAPACITY_SHORT', 'severity': 'warning', 'message': '월 생산능력 부족'}]}
    o = propose(FakeCaller(bench(market__median=4300.0), check), intent='target').offer
    issues = [x.issue for x in o.objections]
    assert issues[0] == '직전 견적 4,100원보다 5.0% 낮다'
    assert issues[1] == '시장 견적 중앙값 4,300원(12곳)보다 낮다'
    assert '월 생산능력 부족' in issues
    assert '원재료 가격이 오르면 손실을 떠안는다' in issues


def test_float_floor_does_not_round_up_by_one_won():
    assert ceil_won(3800 * 0.9) == 3420


def test_tie_goes_to_price_closest_to_target():
    out = propose(FakeCaller(bench()))
    assert out.offer.basis == 'payment' and (out.offer.unit_price, out.offer.payment_days) == (3800, 30)


def test_intent_only_changes_which_base_wins():
    out = propose(FakeCaller(bench(market__median=4300.0)), intent='market')
    assert out.offer.basis == 'market' and out.offer.unit_price == 4300
    assert considered(out, 'target').reason == '요청한 기준(시장가 중심)이 아님'


def test_existing_payment_over_limit_is_capped():
    out = propose(FakeCaller(bench(supplier__payment_days=120)), intent='target')
    assert out.offer.payment_days == 60
    assert any(b.source == 'supplier.payment_days' and b.value == 120 for b in out.offer.rationale)


def test_linkage_exemptions_follow_rule_values():
    assert propose(FakeCaller(bench()), contract_days=60).offer.price_linkage is False
    assert propose(FakeCaller(bench(rfq__annual_volume=100))).offer.price_linkage is False
    assert propose(FakeCaller(bench(supplier__raw_material_ratio=0.05))).offer.price_linkage is False


def test_violation_is_revised_and_rechecked():
    def check(args):
        if not args['price_linkage']:
            return {'verdict_code': 'VIOLATION', 'warnings': [],
                    'violations': [{'code': 'LINKAGE_TERMS_MISSING', 'severity': 'violation', 'message': '연동 없음'}]}
        return {'verdict_code': 'OK', 'violations': [], 'warnings': []}
    caller = FakeCaller(bench(supplier__raw_material_ratio=0.05), check)
    out = propose(caller)
    assert out.offer.price_linkage is True and out.offer.verdict_code == 'OK'
    assert [(r.code, r.field, r.before, r.after) for r in out.offer.revisions] == [
        ('LINKAGE_TERMS_MISSING', 'price_linkage', False, True)]


def test_revise_caps_payment_days():
    c = Candidate(intent='target', label='목표가 중심', unit_price=3800, payment_days=90, price_linkage=True,
                  raw_material_ratio=0.4, energy_ratio=None, contract_days=365, rationale=[])
    revisions = revise(c, [{'code': 'PAYMENT_DAYS_OVER_60'}], RULES)
    assert c.payment_days == 60 and revisions[0].before == 90
    assert revise(c, [{'code': 'SOMETHING_ELSE'}], RULES) is None


def test_violation_is_never_presented_and_no_offer_explains_why():
    unfixable = {'verdict_code': 'VIOLATION', 'warnings': [],
                 'violations': [{'code': 'NEW_RULE', 'severity': 'violation', 'message': '알 수 없는 위반'}]}
    caller = FakeCaller(bench(), lambda args: unfixable)
    out = propose(caller)
    assert out.status == 'no_offer' and out.offer is None
    assert {c.basis for c in out.considered} == {'target', 'market', 'payment'}
    assert all(c.violations for c in out.considered)
    assert out.no_offer_reasons == [f'{label}: 알 수 없는 위반' for label in ('목표가 중심', '시장가 중심', '지급 조건 양보')]
    assert len(checks(caller)) == 3            # 고칠 수 없으면 다시 검사하지 않는다
    assert '제시할 수 있는 협상안이 없습니다' in render(out)


def test_persistent_violation_stops_after_max_rechecks():
    stubborn = {'verdict_code': 'VIOLATION', 'warnings': [],
                'violations': [{'code': 'LINKAGE_TERMS_MISSING', 'severity': 'violation', 'message': '연동 없음'}]}
    caller = FakeCaller(bench(supplier__raw_material_ratio=0.05), lambda args: stubborn)
    out = propose(caller)
    assert out.status == 'no_offer'
    assert len(checks(caller)) == 6            # 처음 1회 + 고친 뒤 1회 (이미 연동이면 더 고칠 것이 없다)


def test_identical_candidates_collapse_to_one_offer():
    b = bench(market__median=3800.0, supplier__last_price=None, supplier__payment_days=30)
    out = propose(FakeCaller(b))
    assert out.offer.basis == 'target'
    assert {(c.basis, c.reason) for c in out.considered} == {('market', '선택한 안과 조건이 같음'),
                                                            ('payment', '선택한 안과 조건이 같음')}


def test_missing_last_quote_and_market():
    b = bench(supplier__last_price=None, market__median=None, market__n=0, market__min=None, market__max=None,
              supplier__payment_days=None, supplier__raw_material_ratio=None)
    out = propose(FakeCaller(b), intent='target')
    o = out.offer
    assert (o.unit_price, o.payment_days, o.price_linkage) == (3800, 60, False)
    assert o.gap_to_last_quote_pct is None and o.gap_to_target_pct == 0.0
    assert [p.unit_price for p in o.price_range] == [3800, 3800, 3800]
    assert any(b.source == 'supplier.raw_material_ratio' and b.value is None for b in o.rationale)
    assert [c.basis for c in out.considered] == ['payment']


def test_check_offer_receives_conditions_date_and_sizes():
    caller = FakeCaller(bench())
    propose(caller, buyer_size={'ksic_code': 'C30399'}, energy_ratio=0.12)
    args = checks(caller)[0]
    assert args['as_of'] == '2026-10-23' and args['buyer_size']['ksic_code'] == 'C30399'
    assert args['raw_material_ratio'] == 0.4 and args['energy_ratio'] == 0.12 and 'supplier_size' not in args


@pytest.mark.parametrize('field,value', [('supplier', 'unknown'), ('payment_days', 0), ('raw_material_ratio', 40),
                                         ('notes', 'x' * 501), ('extra', 1), ('price_linkage', 'no')])
def test_request_rejects_invalid_values(field, value):
    body = {'supplier': 'SUP-0007', 'rfq_id': 'RFQ-2026-001', 'as_of': '2026-10-23', field: value}
    with pytest.raises(ValidationError):
        OfferProposalRequest.model_validate(body)


def test_request_rejects_max_below_target():
    with pytest.raises(ValidationError):
        request(target_price=4000, max_price=3900)


def test_explainer_is_off_by_default_and_optional():
    assert propose(FakeCaller(bench())).explanation is None

    async def explain(proposal):
        return f'{proposal.offer.unit_price}원 제시'
    out = asyncio.run(OfferProposer(FakeCaller(bench()), explainer=explain).propose(request()))
    assert out.explanation == '3800원 제시'


def test_render_shows_one_offer_first():
    text = render(propose(FakeCaller(bench(market__median=4300.0))))
    assert text.splitlines()[2] == '▶ SUP-0007에 단가 3,895원, 지급 45일, 연동 약정 포함, 거래 기간 365일 제시'
    assert '목표 단가 대비 +2.5%, 직전 견적 대비 -5.0%' in text
    assert '양보 순서:' in text and '검토했지만 고르지 않은 후보:' in text
