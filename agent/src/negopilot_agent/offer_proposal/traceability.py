"""조건별 근거와 미확인 항목을 연결하고 양보 대안을 재검사한다."""
from negopilot_agent.legal_assessment import assess_transaction

FIELDS = ('unit_price', 'payment_days', 'price_linkage', 'contract_days')

def attach_condition_basis(offer, assessment, req):
    findings = assessment.get('findings', [])
    for field in FIELDS:
        relevant = [f for f in findings if field in f.get('affected_fields', [])]
        prefixes = {'unit_price': ('request.target_price', 'request.max_price', 'rfq.target_price', 'market.', 'supplier.last_price'),
                    'payment_days': ('request.payment_days', 'supplier.payment_days', 'offer.payment_days', 'rules.PAYMENT'),
                    'price_linkage': ('request.raw_material_ratio', 'supplier.raw_material_ratio', 'rules.MAJOR', 'rules.LINKAGE', 'request.linkage'),
                    'contract_days': ('request.contract_days',)}[field]
        selection = [b.model_dump() for b in offer.rationale if b.source.startswith(prefixes)]
        if field == 'contract_days':
            selection = [{'source': 'request.contract_days' if req.contract_days is not None else 'default.contract_days',
                          'value': offer.contract_days,
                          'note': '검토에 입력한 계약 기간 유지' if req.contract_days is not None else '기본 계약 기간 사용'}]
        reasons = (['선정 근거: ' + '; '.join(b['note'] for b in selection)] if selection else [])
        if relevant:
            reasons.append('검토 근거: ' + ' / '.join(f.get('summary', '') for f in relevant))
        offer.condition_basis.append({
            'field': field, 'value': getattr(offer, field),
            'finding_ids': [f['id'] for f in relevant],
            'evidence_refs': list(dict.fromkeys(str(e) for f in relevant for e in f.get('evidence_refs', []))),
            'law_refs': [r for f in relevant for r in f.get('law_refs', [])],
            'selection_basis': selection,
            'reason': '. '.join(reasons) or '협상에서 선택한 조건값',
            'status': assessment.get('status', 'needs_information'),
            'value_origin': 'negotiation_choice',
        })
    terms = offer.model_dump(include=set(FIELDS) | {'raw_material_ratio', 'energy_ratio', 'linkage_waived_by_agreement'})
    alternatives = []
    # 대안마다 모든 조건을 묶어 다시 검사한다.
    for point in offer.price_range:
        alternatives.append((point.label, terms | {'unit_price': point.unit_price}))
    if offer.payment_days > 30:
        alternatives.append(('지급 단축 검토', terms | {'payment_days': max(30, offer.payment_days - 15)}))
    if offer.contract_days < 730:
        alternatives.append(('계약 기간 연장 검토', terms | {'contract_days': min(730, offer.contract_days * 2)}))
    for label, candidate in alternatives:
        result = assess_transaction(req.transaction, candidate, req.as_of)
        offer.assessed_alternatives.append({'label': label, 'terms': candidate, 'assessment': result,
                                           'requires_agreement': True})
    offer.concessions = [a['label'] + '. ' + ('제안 보류' if a['assessment']['status'] == 'blocked'
                         else '미확인 사항 확인 후 협의' if a['assessment']['status'] != 'ready' else '조건 합의 필요')
                         for a in offer.assessed_alternatives]
