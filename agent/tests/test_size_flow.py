import json
import pytest
from negopilot_shared import DecompositionResult
from negopilot_agent.decomposition.size_facts import explicit_context, preserve_size_facts
from negopilot_agent.generation.answer import Evidence
from negopilot_agent.generation.size_answer import render_size_answer

QUERY = '합성 테스트. 기준일 2026-10-23, SUP-0034, RFQ-2026-001, 단가 3900원, 지급 60일, 연동 약정 미포함, 원재료 비중 40%, 계약기간 365일. 공급자는 주업종 C30399, 평균매출액 1100억원, 자산 2000억원, 독립성 충족, 관계기업 없음, 경과조치 검토 완료, 유예·간주 중소기업 아님, 근거 SYNTHETIC-QA. 구매자도 C30399, 평균매출액 120.01억원, 자산 2000억원, 독립성 충족, 관계기업 없음, 경과조치 검토 완료, 유예·간주 중소기업 아님, 근거 SYNTHETIC-QA. 협상안을 검사하고 구매자 소기업 여부와 연동 예외 판정을 설명해줘.'

def test_restores_dropped_offer_and_both_roles_without_model_facts():
    r=preserve_size_facts(DecompositionResult(status='failed',original_query=QUERY),QUERY,[])
    e=r.sub_queries[0].entities
    assert e.supplier_size.average_revenue_eok==1100
    assert e.buyer_size.average_revenue_eok==120.01
    assert e.buyer_size.assets_eok==2000
    assert e.buyer_size.ksic_code=='C30399'
    assert not e.buyer_size.related_calculation_verified
    assert e.as_of=='2026-10-23'

def test_followup_preserves_user_facts_and_updates_explicit_change():
    q='같은 합성 협상안에서 구매자의 평균매출액만 120억원으로 바꿔 다시 검사해줘. 나머지 조건은 동일해.'
    v=explicit_context(q,[{'role':'user','content':QUERY},{'role':'assistant','content':'구매자는 평균매출액 9999억원'}])
    assert v['buyer_size']['average_revenue_eok']==120
    assert v['buyer_size']['assets_eok']==2000
    assert v['supplier_size']['average_revenue_eok']==1100

def test_unknown_overrides_previous_confirmation():
    v=explicit_context('구매자의 독립성 미확인. 나머지는 동일하게 검사해줘.',[{'role':'user','content':QUERY}])
    assert v['buyer_size']['independence_met'] is None
    assert v['buyer_size']['average_revenue_eok'] == 120.01

def test_new_target_does_not_inherit_other_supplier():
    v=explicit_context('SUP-0099도 같은 조건으로 검사해줘.',[{'role':'user','content':QUERY}])
    assert 'supplier_size' not in v

def test_won_conversion_and_missing_never_fabricated():
    v=explicit_context('구매자는 평균매출액 12,001,000,000원, 자산 미확인',[])
    assert v['buyer_size']=={'average_revenue_eok':120.01,'assets_eok':None}

@pytest.mark.parametrize('value,expected',[(True,'해당'),(False,'해당하지 않음'),(None,'판정 보류(확인 필요)')])
def test_render_three_states_exactly(value,expected):
    e=Evidence(id='E1',sub_query='q1',source='business',kind='company_size',label='기업 규모',
               text=json.dumps({'is_sme':True,'is_small_business':value,'missing':['independence_met'] if value is None else []}),meta={'role':'buyer'})
    a=render_size_answer([e],[],'검사해줘')
    assert f'소기업 {expected}' in a.text
    assert len(a.citations)==1
    if value is None: assert '소기업 해당하지 않음' not in a.text

def test_unclear_new_independence_does_not_reuse_old_true():
    v=explicit_context('구매자의 독립성 정보는 아직 없어. 나머지는 동일하게 검사해줘.',[{'role':'user','content':QUERY}])
    assert v['buyer_size']['independence_met'] is None
    assert v['buyer_size']['average_revenue_eok'] == 120.01

def test_multiple_targets_are_not_merged():
    assert explicit_context(QUERY+' SUP-0002도 검사해줘.',[]) == {}

def test_explicit_copy_same_buyer_details():
    q=QUERY.split('구매자도')[0]+'구매자도 C30399이고 평균매출액만 120억원, 나머지 자산·독립성·관계기업·경과조치·간주 여부·근거는 동일해.'
    v=explicit_context(q,[])
    assert v['buyer_size']['ksic_code']=='C30399'
    assert v['buyer_size']['assets_eok']==2000
    assert v['buyer_size']['independence_met'] is True
    assert v['buyer_size']['average_revenue_eok']==120

async def test_complete_labelled_offer_does_not_depend_on_model():
    from negopilot_agent.decomposition.decomposer import QueryDecomposer
    class NoModel:
        async def generate(self,*args): pytest.fail('Explicit offer must not use a model')
    result=await QueryDecomposer(backend=NoModel()).decompose(QUERY)
    assert result.status=='ok'
    assert len(result.sub_queries)==1
    assert result.sub_queries[0].entities.buyer_size.average_revenue_eok==120.01

def test_explicit_invalid_range_requests_correction():
    from negopilot_agent.decomposition.size_facts import explicit_offer_result
    result=explicit_offer_result(QUERY.replace('40%','140%'),[])
    assert result.status=='clarify'

def test_mixed_source_request_is_not_swallowed():
    from negopilot_agent.decomposition.size_facts import explicit_offer_result
    assert explicit_offer_result(QUERY+' 관련 조문 원문도 찾아줘.',[]) is None
