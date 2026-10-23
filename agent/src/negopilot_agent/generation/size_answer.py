"""Render legal eligibility's three states directly from tool evidence.

No LLM paraphrase may turn unknown into false or reinterpret a checked result.
"""
import json
from negopilot_agent.generation.answer import Answer
from negopilot_agent.generation.verify import render_citations

LABELS = {'average_revenue_eok':'평균매출액(억원)', 'assets_eok':'자산총액(억원)',
          'independence_met':'독립성 요건', 'has_related_enterprises':'관계기업 유무'}

def state_label(value):
    return '해당' if value is True else '해당하지 않음' if value is False else '판정 보류(확인 필요)'

def render_size_answer(evidence, notices, query):
    if not any(e.kind=='company_size' for e in evidence): return None
    lines=['입력한 사실에 따른 기업 규모와 협상안 검사 결과입니다.']
    for e in evidence:
        cite = f' [{e.id}]' if e.citable else ''
        if e.kind=='company_size':
            a=json.loads(e.text)
            role={'supplier':'공급자','buyer':'구매자'}.get(e.meta.get('role'),'기업')
            lines.append(f"- {role}: 중소기업 {state_label(a.get('is_sme'))}, 소기업 {state_label(a.get('is_small_business'))}.{cite}")
            if a.get('missing'):
                missing=', '.join(LABELS.get(x,x) for x in a['missing'])
                lines.append(f"  확인할 정보: {missing}. 판정 보류는 ‘해당하지 않음’을 뜻하지 않습니다.{cite}")
            if a.get('thresholds'):
                t=a['thresholds']
                lines.append(f"  적용 업종 {a.get('ksic_code')}: 평균매출액 기준 중소기업 {t['sme_eok']}억원 이하, 소기업 {t['small_eok']}억원 이하. 매출 외 요건도 함께 검사합니다.{cite}")
        elif e.kind=='offer_verdict':
            a=json.loads(e.text)
            lines.append(f"- {e.label}: 검사 범위 내 결과는 ‘{a.get('verdict')}’입니다.{cite}")
        elif e.kind=='offer_finding':
            text=e.text
            for key,label in LABELS.items(): text=text.replace(key,label)
            lines.append(f'- {text}{cite}')
    size_queries={e.sub_query for e in evidence if e.kind=='company_size'}
    # Mixed requests retain other subqueries' source text without paraphrasing
    # the eligibility decision. Auxiliary law references are available below.
    for e in evidence:
        if e.sub_query not in size_queries and e.citable:
            lines.append(f'함께 조회된 자료(적용 여부는 별도 확인): {e.label} [{e.id}]')
    if any(x in query for x in ('이전','달라','변경','바꿔','다시')):
        lines.append('현재 입력에 대한 재검사 결과입니다. 이전 판정과의 비교는 이 답변에서 확정하지 않습니다.')
    lines.append('기업 규모 충족만으로 하도급법 적용이 확정되지는 않습니다. 위탁 유형·원사업자 요건과 거래의 다른 예외는 별도로 확인해야 합니다.')
    lines.extend(f'※ {n}' for n in notices)
    text,citations=render_citations('\n\n'.join(lines),evidence)
    return Answer(kind='answer',text=text,citations=citations,grounded=bool(citations),notices=notices,evidence=evidence)
