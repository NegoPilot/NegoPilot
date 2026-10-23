"""Preserve explicit, role-labelled size facts; never use assistant text as facts.

This deliberately recognizes a bounded input vocabulary. Unrecognized or absent
facts stay unknown, rather than trusting a model to invent a legal prerequisite.
"""
import re
from decimal import Decimal
from pydantic import ValidationError
from negopilot_shared import DecompositionResult

ROLE = re.compile(r'(공급자|구매자)(?:는|도|의|가|:|\s)')
NUM = r'([\d,]+(?:\.\d+)?)'
FIELDS = {'평균매출액': 'average_revenue_eok', '자산': 'assets_eok'}

def facts(text):
    out = {}
    code = re.search(r'(?<![A-Za-z0-9])C\d{2}(?:\d{3})?(?![A-Za-z0-9])', text)
    if code: out['ksic_code'] = code.group()
    for label, field in FIELDS.items():
        m = re.search(label + r'(?:총액)?(?:은|는|이|만)?\s*' + NUM + r'\s*(억원|억\s*원|원)', text)
        if m:
            value = Decimal(m[1].replace(',', ''))
            out[field] = float(value if '억' in m[2] else value / Decimal(100000000))
        if re.search(label + r'[^,.;\n]{0,15}(?:미확인|모름|불명|확인되지|알 수 없)', text): out[field] = None
    for field, label, yes, no in [
        ('independence_met','독립성','충족','미충족'),
        ('has_related_enterprises','관계기업','있음','없음'),
        ('transition_reviewed','경과조치','검토 완료','미검토'),
    ]:
        m = re.search(label + r'\s*(?:은|는|이)?\s*(' + no + '|' + yes + r'|미확인|모름|불명)', text)
        if m: out[field] = True if m[1] == yes else False if m[1] == no else (False if field == 'transition_reviewed' else None)
    if re.search(r'(?:유예[·/ㆍ ]*간주|간주)[^,.;\n]{0,12}아님',text): out['deemed_sme'] = False
    m = re.search(r'근거\s*[:：]?\s*([^,.;\n]+)', text)
    if m and not re.search(r'동일|없음|미확인',m[1]): out['evidence_reference'] = m[1].strip()
    if re.search(r'근거\s*(?:없음|미확인)',text): out['evidence_reference'] = None
    # A newly mentioned but unrecognized value must invalidate an old value.
    for label, field in dict(FIELDS, 독립성='independence_met', 관계기업='has_related_enterprises', 경과조치='transition_reviewed').items():
        clauses = [x for x in re.split(r'[.;,\n]',text) if label in x]
        if clauses and field not in out and not any(re.search(r'동일|같은',x) for x in clauses):
            out[field] = False if field == 'transition_reviewed' else None
    return out

def explicit_context(query, history):
    state = {}
    if any(len(set(re.findall(p, query))) > 1 for p in (r'SUP-\d{4,}', r'RFQ-\d{4}-\d{3}')):
        return state
    # Reuse prior USER facts only when the current request explicitly continues them.
    continues = bool(re.search(r'동일|같은|나머지|앞선|이전', query))
    texts = [h['content'] for h in (history or []) if h.get('role') == 'user'] if continues else []
    for text in [*texts, query]:
        ids = dict((k, m.group()) for k, pattern in [('supplier',r'(?<![A-Za-z0-9])SUP-\d{4,}(?![A-Za-z0-9])'),('rfq_id',r'(?<![A-Za-z0-9])RFQ-\d{4}-\d{3}(?![A-Za-z0-9])')]
                   if (m := re.search(pattern,text)))
        if any(k in state and state[k] != v for k,v in ids.items()): state = {}
        state.update(ids)
        for k,pattern in [('unit_price',r'단가\s*'+NUM+r'\s*원'),('payment_days',r'(?:지급|결제)(?:기한| 조건)?\s*'+NUM+r'\s*일'),('contract_days',r'계약기간\s*'+NUM+r'\s*일')]:
            if m := re.search(pattern,text):
                value=Decimal(m[1].replace(',',''))
                if value == value.to_integral_value(): state[k] = int(value)
        if m := re.search(r'원재료 비중\s*'+NUM+r'\s*%',text): state['raw_material_ratio'] = float(Decimal(m[1].replace(',',''))/100)
        if m := re.search(r'연동 약정\s*(미포함|포함)',text): state['price_linkage'] = m[1] == '포함'
        if m := re.search(r'기준일\s*(\d{4}-\d{2}-\d{2})',text): state['as_of'] = m[1]
        roles = list(ROLE.finditer(text))
        for i,m in enumerate(roles):
            part = text[m.end():roles[i+1].start() if i+1<len(roles) else len(text)]
            parsed = facts(part)
            if not parsed: continue
            key = 'supplier_size' if m[1]=='공급자' else 'buyer_size'
            # "구매자도 ... 나머지는 동일" explicitly copies supplier facts, then overrides.
            base = state.get(key,{}) if re.search(r'동일|같은|나머지|바꿔|변경',text) else {}
            if key=='buyer_size' and m.group().startswith('구매자도') and re.search(r'동일',part) and re.search(r'나머지',part):
                base = state.get('supplier_size',{})
            state[key] = dict(base) | parsed
    return state

def preserve_size_facts(result, query, history):
    state = explicit_context(query,history)
    if not any(k in state for k in ('supplier_size','buyer_size')): return result
    required = {'supplier','rfq_id','unit_price','payment_days','price_linkage'}
    data = result.model_dump(mode='json',by_alias=True)
    offers = [sq for sq in data['sub_queries'] if sq['intent']=='offer_check']
    # Restore a dropped check only when the user supplied the complete explicit offer.
    if not offers and required <= state.keys() and '검사' in query:
        if data['status'] != 'ok': data.update(status='ok',sub_queries=[],clarification=None)
        if len(data['sub_queries']) >= 5: return result
        sq = dict(id=f"q{len(data['sub_queries'])+1}",question=query,original_text=query,
                  intent='offer_check',grounding='required',confidence=1,entities={},inputs=[],depends_on=[])
        data['sub_queries'].append(sq); offers=[sq]
    for sq in offers:
        # Do not apply one company's facts to another target or fanout.
        if sq['inputs']: continue
        if any(sq['entities'].get(k) not in (None,v) for k,v in state.items() if k in ('supplier','rfq_id')): continue
        sq['entities'].update(state)
    try:
        return DecompositionResult.model_validate(data)
    except ValidationError:
        return DecompositionResult(status='clarify',original_query=query,clarification={
            'reason':'명시된 입력이 계약 형식 또는 허용 범위를 벗어남',
            'question':'기업 규모와 협상 조건의 숫자·단위를 다시 확인해 주세요.',
            'options':['입력값 수정','검사 취소']})


def explicit_offer_result(query, history):
    """Route a fully specified single offer without probabilistic classification.

    Requests also asking for source retrieval/ranking keep the ordinary router.
    """
    if '검사' not in query or re.search(r'검색|찾아|조문|원문|추천|목록',query): return None
    state=explicit_context(query,history)
    if not {'supplier','rfq_id','unit_price','payment_days','price_linkage'} <= state.keys(): return None
    if not any(k in state for k in ('supplier_size','buyer_size')): return None
    return preserve_size_facts(DecompositionResult(status='failed',original_query=query),query,history)
