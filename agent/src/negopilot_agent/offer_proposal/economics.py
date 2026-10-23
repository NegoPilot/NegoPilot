"""입력 단가와 물량 및 조달률로 현금흐름을 비교한다."""
from decimal import Decimal, ROUND_HALF_UP


def won(value):
    return int(Decimal(str(value)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def compare(terms, bench, facts, quantity=None):
    q = quantity if quantity is not None else facts.committed_quantity
    qty_source = 'user_volume_scenario' if quantity is not None else 'transaction.committed_quantity'
    if q is None:
        qty_source = 'unknown'
    price, days = terms['unit_price'], terms['payment_days']
    old_price = facts.previous_unit_price or bench['supplier'].get('last_price')
    old_days = facts.previous_payment_days or bench['supplier'].get('payment_days')
    total = price * q if q is not None else None
    baseline = old_price * q if old_price is not None and q is not None else None
    saving = baseline - total if baseline is not None and total is not None else None
    advance = old_days - days if old_days is not None else None
    def finance(rate):
        if rate is None or total is None or advance is None:
            return None
        return won(Decimal(total) * Decimal(str(rate)) * Decimal(advance) / Decimal(365))
    buyer_cost = finance(facts.buyer_financing_rate)
    supplier_benefit = finance(facts.supplier_financing_rate)
    missing = []
    for key, value in [('확정 구매물량', q), ('비교 단가', old_price), ('기존 지급일수', old_days),
                       ('구매자 연 자금조달률', facts.buyer_financing_rate),
                       ('공급자 연 자금조달률', facts.supplier_financing_rate)]:
        if value is None:
            missing.append(key)
    return {'quantity': q, 'quantity_source': qty_source, 'unit_price': price, 'payment_days': days,
        'contract_days': terms['contract_days'], 'purchase_amount': total, 'baseline_amount': baseline,
        'buyer_price_saving': saving, 'supplier_revenue_change': -saving if saving is not None else None,
        'payment_advance_days': advance, 'buyer_financing_cost': buyer_cost,
        'supplier_financing_saving': supplier_benefit,
        'buyer_net_saving': saving - buyer_cost if saving is not None and buyer_cost is not None else None,
        'supplier_revenue_finance_change': -saving + supplier_benefit if saving is not None and supplier_benefit is not None else None,
        'missing': missing}


def selection_key(candidate, req, bench, target):
    from .generator import rank_key
    if req.transaction is None or req.transaction.committed_quantity is None:
        return rank_key(candidate, target, req.intent)
    row = compare(candidate.model_dump(), bench, req.transaction)
    # 조달률을 모르면 명목 구매금액만 비교한다.
    adjusted = row['purchase_amount'] + (row['buyer_financing_cost'] if row['buyer_financing_cost'] is not None else 0)
    return (candidate.intent != req.intent, len(candidate.warnings), adjusted, abs(candidate.unit_price-target))


def build_comparison(offer, candidates, bench, req, assessment):
    f = req.transaction
    rows, seen = [], set()
    def add(label, terms, selected=False, status='ready', quantity=None):
        key=(terms['unit_price'],terms['payment_days'],terms['contract_days'],quantity or f.committed_quantity)
        if key in seen:
            return
        seen.add(key)
        rows.append({'label':label,'selected':selected,'status':status,**compare(terms,bench,f,quantity)})
    add('현재 후보 중 우선 검토안', offer.model_dump(), True, assessment.get('status','needs_information'))
    for c in candidates:
        add(c.label,c.model_dump(),status=c.assessment.get('status','needs_information'))
    for a in offer.assessed_alternatives:
        add(a['label'],a['terms'],status=a['assessment']['status'],quantity=a.get('quantity'))
    selected = rows[0]
    for row in rows:
        row['quantity_change_vs_selected'] = row['quantity'] - selected['quantity'] if row['quantity'] is not None and selected['quantity'] is not None else None
        row['spend_change_vs_selected'] = row['purchase_amount'] - selected['purchase_amount'] if row['purchase_amount'] is not None and selected['purchase_amount'] is not None else None
    criterion = ('확정 물량 기준 구매금액과 입력 자금조달률의 조기 지급 비용' if selected['buyer_financing_cost'] is not None
                 else '확정 물량 기준 명목 구매금액 (기존 지급일수 또는 조달률 미입력으로 금융비용 미반영)') if f.committed_quantity else '목표 단가와의 차이 (확정 물량 미입력)'
    reasons = ['현재 생성한 후보와 입력 정보 범위에서 정한 우선 검토안입니다. 전체 시장의 최적 여부와 상대방 수락 여부는 확인하지 않았습니다.',
               f'위반 후보와 보류 후보를 제외했습니다. 요청한 협상 방향을 먼저 비교하고 확인 필요 건수와 {criterion} 순으로 비교했습니다.']
    if selected['buyer_price_saving'] is not None:
        reasons.append(f"같은 물량의 비교 단가 대비 구매금액 차이는 {selected['buyer_price_saving']:,}원입니다. 양수는 구매자 절감이며 공급자에게는 같은 금액의 매출 감소입니다.")
    if selected['payment_advance_days'] is not None:
        reasons.append(f"기존 지급조건 대비 {selected['payment_advance_days']}일 빠른 지급입니다. 양수이면 공급자는 대금을 빨리 회수하고 구매자는 현금을 먼저 지급합니다.")
    if selected['buyer_net_saving'] is not None:
        reasons.append(f"입력 조달률로 계산한 구매자 순절감 추정은 {selected['buyer_net_saving']:,}원입니다.")
    if selected['supplier_revenue_finance_change'] is not None:
        reasons.append(f"공급자의 매출과 금융효과 합계는 {selected['supplier_revenue_finance_change']:,}원입니다. 원가를 제외한 이익 추정이 아닙니다.")
    if selected['buyer_net_saving'] is not None and selected['buyer_net_saving'] < 0:
        reasons.append(f"구매자에게 불리한 점: 비교 거래 대비 가격과 금융 부담이 {-selected['buyer_net_saving']:,}원 증가합니다. 납기 준수와 품질 책임 및 공급물량 확보 등의 반대급부와 그 가치를 확인한 뒤 채택 여부를 정해야 합니다.")
    elif selected['buyer_price_saving'] is not None and selected['buyer_price_saving'] < 0:
        reasons.append(f"구매자에게 불리한 점: 구매금액이 {-selected['buyer_price_saving']:,}원 증가하며 금융효과는 미확인입니다. 기존 지급조건과 조달률 및 공급물량 확보 등의 반대급부를 확인해야 합니다.")
    if selected['supplier_revenue_change'] is not None and selected['supplier_revenue_change'] < 0:
        reasons.append('공급자에게 불리한 점: 같은 물량 기준 매출이 감소합니다. 원가와 생산여력을 확인해야 합니다. 확정 발주 또는 조기 지급으로 보완할 수 있는지도 상대방과 확인해야 합니다.')
    reasons.append('추가 확보 조건: 비교 견적의 사양과 납기가 일치하는지 확인해야 합니다. 확정 물량과 납기 준수 및 품질 책임의 합의와 증빙도 필요합니다. 이 조건의 가치와 상대방 수락 여부는 현재 계산하지 않았습니다.')
    if (f.volume_option_quantity is None) != (f.volume_option_unit_price is None):
        selected['missing'].append('물량 대안은 수량과 단가를 함께 입력')
    return {'rows': rows,'selection_reasons':reasons,'selected':selected,
        'baseline': {'unit_price':f.previous_unit_price or bench['supplier'].get('last_price'),
                     'price_source':'transaction.previous_unit_price' if f.previous_unit_price else 'supplier.last_price',
                     'payment_days':f.previous_payment_days or bench['supplier'].get('payment_days'),
                     'payment_source':'transaction.previous_payment_days' if f.previous_payment_days else 'supplier.payment_days',
                     'buyer_financing_rate':f.buyer_financing_rate,'supplier_financing_rate':f.supplier_financing_rate},
        'assumptions': ['비교 단가의 사양과 납기 및 물량 조건이 같다고 가정했습니다. 실제 거래조건을 추가 확인해야 합니다.',
            '금융효과 = 해당 안의 구매금액 × 입력 연 조달률 × 기존 대비 단축 일수 ÷ 365. 전체 금액에 같은 지급조건을 적용한 단리 추정입니다.',
            '금액은 원 단위로 반올림했습니다. 세금과 운송비 및 재고 비용은 제외합니다. 불량 비용과 생산원가 및 가격 연동에 따른 미래 변동도 제외합니다.',
            '공급자 매출 감소는 이익 감소액과 다릅니다. 원가 자료 없이 공급자의 수익성이나 수락 가능성을 확정하지 않습니다.',
            '계약 기간 연장만으로 구매물량을 늘리지 않습니다. 물량 대안은 별도 합의가 필요한 가정이며 선정 대상에 자동 포함되지 않습니다.',
            '기존 지급조건은 비교용 이력이며 법적으로 허용되는 기한이라는 뜻이 아닙니다.'],
        'missing':selected['missing']}


def selection_reason(candidate, chosen, req, bench, target):
    from .generator import lost_reason
    if req.transaction is None or req.transaction.committed_quantity is None:
        return lost_reason(candidate, chosen, target, req.intent)
    if candidate.terms() == chosen.terms() or (req.intent and chosen.intent == req.intent != candidate.intent) or len(candidate.warnings) > len(chosen.warnings):
        return lost_reason(candidate, chosen, target, req.intent)
    candidate_cost = selection_key(candidate, req, bench, target)[2]
    chosen_cost = selection_key(chosen, req, bench, target)[2]
    basis = '금융비용 반영 구매부담' if compare(candidate.model_dump(), bench, req.transaction)['buyer_financing_cost'] is not None else '명목 구매금액'
    if candidate_cost > chosen_cost:
        return f'{basis} {candidate_cost:,}원으로 선정안({chosen_cost:,}원)보다 큼'
    return '구매부담이 같아 목표 단가 차이와 후보 순서로 선정'
