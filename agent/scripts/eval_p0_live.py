"""Read-only live MCP/router evaluation. Synthetic company facts; no DB updates.
From repository root: PYTHONPATH=agent/src:shared/src agent/.venv/bin/python agent/scripts/eval_p0_live.py
"""
import asyncio, json, math, statistics, time
from datetime import datetime, timezone
from pathlib import Path
from negopilot_agent.routing import McpToolCaller, Router
from negopilot_shared import DecompositionResult
from negopilot_agent.generation.evidence import from_search_regulation, from_check_offer
DAY='2026-10-23'
SEARCH={
'sme_annex_1':['자동차 제조업 중소기업 평균매출액 규모 기준','자동차용 신품 의자 제조업 C30393 1500억원'],
'sme_annex_2':['관계기업 평균매출액 합산 지배기업 종속기업','간접 지배 손자기업 주식 소유비율 계산'],
'sme_annex_3':['소기업 업종별 평균매출액 규모 기준','자동차 제조업 소기업 120억원 기준'],
'manufacturing_scope':['제조위탁 대상 물품 범위 고시','물품의 제조 수리 판매를 업으로 하는 사업자 제조위탁'],
'linkage_guideline':['연동제 계약 쪼개기 탈법행위','연동제 기준 지표 조정주기 성실한 협의 운영지침'],
'late_interest':['선급금 지연지급 지연이율 15.5','하도급대금 늦게 지급하면 적용되는 고시 이자율'],
'bill_discount':['어음 하도급대금 할인율 고시','어음으로 대금을 줄 때 연 7.5 할인료'],
'price_reduction':['일률적으로 납품 단가를 인하하는 행위 심사지침','이미 결정한 하도급대금을 소급하여 감액'],
'restricted_information':['협력사 원가계산서 원가명세서 요구 금지','다른 거래처 납품가격과 매출명세서 요구'],
'linkage_templates':['표준 연동계약서와 미연동계약서','미연동 합의 취지 사유 표준계약서 양식'],
'energy_linkage_guide':['에너지 경비 납품대금 연동 실무 가이드','전기 가스 에너지 비용 연동 실무']}
BASE=dict(ksic_code='C30399',average_revenue_eok=1100,assets_eok=2000,independence_met=True,
 has_related_enterprises=False,transition_reviewed=True,deemed_sme=False,evidence_reference='SYNTHETIC EVALUATION INPUT; not actual company information')
SIZES=[
('자동차1001',{'average_revenue_eok':1001},True,False),('자동차1200',{'average_revenue_eok':1200},True,False),
('자동차초과',{'average_revenue_eok':1200.01},False,False),('의자1500',{'ksic_code':'C30393','average_revenue_eok':1500},True,False),
('의자초과',{'ksic_code':'C30393','average_revenue_eok':1500.01},False,False),('금속1800',{'ksic_code':'C24100','average_revenue_eok':1800},True,False),
('전자1001',{'ksic_code':'C26100','average_revenue_eok':1001},False,False),('소기업120',{'average_revenue_eok':120},True,True),
('소기업초과',{'average_revenue_eok':120.01},True,False),('자산경계',{'assets_eok':5000},False,False),
('독립성미충족',{'independence_met':False},False,False),
('관계기업초과',{'has_related_enterprises':True,'related_aggregate_revenue_eok':1201,'related_calculation_verified':True},False,False),
('매출누락',{'average_revenue_eok':None},None,None),('자산누락',{'assets_eok':None},None,None),
('독립성누락',{'independence_met':None},None,None),('관계기업미검증',{'has_related_enterprises':True},None,None),
('경과조치누락',{'transition_reviewed':False},None,None),('근거누락',{'evidence_reference':None},None,None),
('세부업종불명',{'ksic_code':'C30'},None,None),('철도의자',{'ksic_code':'C31202','seat_manufacturing':True,'average_revenue_eok':1500},True,False),
('의자불명',{'ksic_code':'C31202'},None,None),('유예',{'average_revenue_eok':1300,'deemed_sme':True},True,None),
('미지원업종',{'ksic_code':'G'},None,None),('과거판미수집',{},None,None,'2025-01-01')]
def decomp(intent,q,entities):
 return DecompositionResult.model_validate({'status':'ok','original_query':q,'sub_queries':[dict(id='q1',question=q,original_text=q,intent=intent,grounding='required',confidence=1,entities=entities)]})
def lat(v):
 v=sorted(v);return {'n':len(v),'p50_ms':round(statistics.median(v),2),'p95_ms':round(v[math.ceil(.95*len(v))-1],2)}
async def main():
 c=McpToolCaller(urls={'law-mcp':'http://localhost:9103/mcp','business-mcp':'http://localhost:9102/mcp'})
 o={'evaluated_at':datetime.now(timezone.utc).isoformat(),'as_of':DAY,'search':[],'size':[],'integration':[],
 'scope':'Live HTTP MCP via service McpToolCaller and Router; synthetic size inputs; no DB writes; no browser or LLM generation evaluation'}
 times={'search':[],'size':[]}
 async with c.session():
  o['catalog_count']=len(await c('law-mcp','list_supporting_documents',{'as_of':DAY}))
  for repeat in range(3):
   for expected,queries in SEARCH.items():
    for q in queries:
     t=time.perf_counter();hits=await c('law-mcp','search_regulation',{'query':q,'as_of':DAY,'top_k':5});times['search'].append((time.perf_counter()-t)*1000)
     if repeat==0:o['search'].append(dict(query=q,expected=expected,rank=next((i+1 for i,h in enumerate(hits) if h.get('document_id')==expected),None),traceable=all(h.get('citation') and h.get('source_url') for h in hits),hits=[h.get('document_id') or h.get('article_no') for h in hits]))
  for name,patch,sme,small,*day in SIZES:
   t=time.perf_counter();r=await c('business-mcp','assess_company_size',{'company':BASE|patch,'as_of':day[0] if day else DAY});times['size'].append((time.perf_counter()-t)*1000)
   o['size'].append(dict(case=name,expected_sme=sme,expected_small=small,actual_sme=r['is_sme'],actual_small=r['is_small_business'],passed=r['is_sme']==sme and r['is_small_business']==small))
  # Separate queries added after the initial tuning; do not tune using these results.
  holdout = [
   ('자동차 부품업체 매출 1100억이면 중소기업 기준에 들어가나','sme_annex_1'),
   ('모회사와 자회사 매출을 지분 비율로 계산하는 별표','sme_annex_2'),
   ('소기업 해당 여부를 확인하는 업종별 기준표','sme_annex_3'),
   ('하도급법상 제조를 맡기는 물품의 종류는 어디에 있나','manufacturing_scope'),
   ('원재료 가격 하락 시에만 연동하기로 하면 가능한가','linkage_guideline'),
   ('선급금을 늦게 지급할 때 적용할 지연이율','late_interest'),
   ('어음 결제 할인료 산정에 사용하는 할인율','bill_discount'),
   ('협의 없이 일방적으로 납품대금을 깎는 행위의 심사 기준','price_reduction'),
   ('협력사의 거래처 명부와 경영전략을 요구해도 되나','restricted_information'),
   ('표준 미연동계약서를 작성할 때 사유를 적는 서식','linkage_templates'),
   ('연료 열 전기 경비를 반영하는 연동제 실무 안내서','energy_linkage_guide')]
  o['holdout'] = []
  for q,expected in holdout:
   hits=await c('law-mcp','search_regulation',{'query':q,'as_of':DAY,'top_k':5})
   o['holdout'].append(dict(query=q,expected=expected,rank=next((i+1 for i,h in enumerate(hits) if h.get('document_id')==expected),None),hits=[h.get('document_id') or h.get('article_no') for h in hits]))
  rfqs=await c('business-mcp','list_rfqs',{});rank=await c('business-mcp','rank_suppliers',{'rfq_id':rfqs[0]['rfq_id'],'top_n':1});rfq=rank['rfq']
  args=dict(supplier=rank['ranked'][0]['supplier_id'],rfq_id=rfq['rfq_id'],unit_price=rfq['target_price'],payment_days=60,price_linkage=False,raw_material_ratio=.4,contract_days=365,as_of=DAY)
  for name,extra,expected in [('검증소기업예외',{'supplier_size':BASE,'buyer_size':BASE|{'average_revenue_eok':120}},'LINKAGE_EXEMPT'),('미검증소기업주장',{'buyer_is_small_business':True},'BUYER_SIZE_UNVERIFIED'),('기업규모누락',{},'SME_STATUS_UNKNOWN')]:
   route=await Router(c).route(decomp('offer_check',name,args|extra));oc=route.outcomes[0];call=next(x for x in oc.calls if x.tool=='check_offer');r=call.result or {};codes=[f['code'] for k in ('info','warnings','violations') for f in r.get(k,[])]
   o['integration'].append(dict(case=name,passed=oc.status=='ok' and expected in codes,codes=codes,forwarded_size=call.args.get('supplier_size') is not None,size_evidence=any(e['kind']=='company_size' for e in from_check_offer(r))))
  for expected,queries in SEARCH.items():
   for q in queries:
    route=await Router(c).route(decomp('law_check',q,{'as_of':DAY}));oc=route.outcomes[0];ev=from_search_regulation(oc.calls[0].result or [])
    o['integration'].append(dict(case='route: '+q,passed=oc.status=='ok' and any(e['meta'].get('document_id')==expected for e in ev)))
 n=sum(map(len,SEARCH.values()));o['metrics']={'hit_at_1':sum(x['rank']==1 for x in o['search'])/n,'hit_at_5':sum(x['rank'] is not None for x in o['search'])/n,'mrr_at_5':sum(1/x['rank'] if x['rank'] else 0 for x in o['search'])/n,'traceability':sum(x['traceable'] for x in o['search'])/n,'size_accuracy':sum(x['passed'] for x in o['size'])/len(SIZES),'abstention_accuracy':sum(x['passed'] for x in o['size'] if x['expected_sme'] is None)/sum(x[2] is None for x in SIZES),'integration_pass':sum(x['passed'] for x in o['integration']),'integration_total':len(o['integration']),'latency':{k:lat(v) for k,v in times.items()}}
 o['metrics']['holdout_hit_at_5']=sum(x['rank'] is not None for x in o['holdout'])/len(o['holdout'])
 path=Path(__file__).resolve().parents[1]/'eval_results/p0-live-2026-10-23/results.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(o,ensure_ascii=False,indent=2)+'\n')
 render_report(o,path.with_suffix('.html'))
 print(json.dumps(o['metrics'],ensure_ascii=False,indent=2));print('Misses:',[(x['query'],x['hits']) for x in o['search'] if x['rank'] is None]);print(path)
def render_report(o, path):
 from html import escape
 m=o['metrics']
 n=len(o['search']); hn=len(o['holdout']); sn=len(o['size']); an=sum(x['expected_sme'] is None for x in o['size'])
 def table(headers, rows):
  return '<table><thead><tr>'+''.join('<th>'+escape(str(x))+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+escape(str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table>'
 summary=[['공식 자료 등록',str(o['catalog_count'])+'개 묶음'],['개발 질문 Hit@1',f"{m['hit_at_1']:.1%} ({sum(x['rank']==1 for x in o['search'])}/{n})"],['개발 질문 Hit@5',f"{m['hit_at_5']:.1%} ({sum(x['rank'] is not None for x in o['search'])}/{n})"],['MRR@5',f"{m['mrr_at_5']:.3f}"],['추가 질문 Hit@5',f"{m['holdout_hit_at_5']:.1%} ({sum(x['rank'] is not None for x in o['holdout'])}/{hn})"],['출처·인용값 포함률',f"{m['traceability']:.1%}"],['규모 기대값 일치',f"{m['size_accuracy']:.1%} ({sum(x['passed'] for x in o['size'])}/{sn})"],['보류 기대값 일치',f"{m['abstention_accuracy']:.1%} ({sum(x['passed'] for x in o['size'] if x['expected_sme'] is None)}/{an})"],['서비스 라우팅 통합',f"{m['integration_pass']}/{m['integration_total']}"],['검색 p50 / p95',f"{m['latency']['search']['p50_ms']} / {m['latency']['search']['p95_ms']} ms"],['규모 판정 p50 / p95',f"{m['latency']['size']['p50_ms']} / {m['latency']['size']['p95_ms']} ms"]]
 body='<h1>NegoPilot P0 실서버 검증</h1><p>'+escape(o['evaluated_at'])+'</p><p>포트 9102·9103의 실제 HTTP MCP를 서비스 McpToolCaller·Router로 호출했다. 기업 사실은 합성 입력이며 DB에 저장하지 않았다.</p>'
 body+='<h2>평가지표</h2>'+table(['지표','측정값'],summary)
 body+='<p>Hit@5: 기대 자료가 상위 5개 결과에 포함된 질문 비율. MRR@5: 기대 자료 최초 순위의 역수 평균(없으면 0). 출처 포함률은 필드 존재 검사이며 법률 해석의 정확도 점수가 아니다.</p>'
 body+='<p>개발 질문 22개에서 두 실패 표현을 보완한 뒤 측정했다. 이후 추가 질문 11개는 결과를 보고 검색을 다시 조정하지 않았다. 독립된 외부 평가셋은 아니다. 소표본·단일 로컬 호스트·순차 요청·연결 재사용 측정이며 LLM 및 웹 요청 전체 지연시간은 포함하지 않는다.</p>'
 body+='<h2>개발 질문 검색 결과</h2>'+table(['질문','기대 자료','순위'],[[x['query'],x['expected'],x['rank'] or '상위 5개 없음'] for x in o['search']])
 body+='<h2>별도 추가 질문</h2>'+table(['질문','기대 자료','순위'],[[x['query'],x['expected'],x['rank'] or '상위 5개 없음'] for x in o['holdout']])
 body+='<h2>기업 규모 경계값·정보 부족</h2>'+table(['사례','기대 중소/소기업','실제 중소/소기업','통과'],[[x['case'],str((x['expected_sme'],x['expected_small'])),str((x['actual_sme'],x['actual_small'])),x['passed']] for x in o['size']])
 body+='<h2>적용 범위와 한계</h2><p>제조업 및 검증된 기준일의 규모 규칙 검증이다. 법무 전문가의 독립 판정이나 실제 기업확인서 진위 검증이 아니다. 기존 상시 MCP 서버는 새 체크아웃으로 재시작했다. 웹·API 서버는 실행 중이지 않아 브라우저 전체 흐름과 LLM 분해·최종 답변 품질은 이번 평가에 포함하지 않았다. 별도 추가 질문 중 소기업 업종별 기준표 질문은 원하는 별표가 상위 5개에 나오지 않아 개선 과제로 남겼다.</p>'
 path.write_text('<!doctype html><html lang="ko"><meta charset="utf-8"><title>NegoPilot P0 실서버 검증</title><style>body{font:16px/1.6 system-ui;max-width:1100px;margin:48px auto;padding:0 24px;color:#1b263b}table{border-collapse:collapse;width:100%;margin:20px 0}th,td{border:1px solid #d6dee8;padding:10px;text-align:left}th{background:#eef3f8}h2{margin-top:36px}</style>'+body+'</html>')

if __name__=='__main__':asyncio.run(main())
