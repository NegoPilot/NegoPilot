import pytest
from pydantic import ValidationError
from negopilot_shared.transaction import TransactionFacts
from negopilot_agent.offer_proposal.economics import compare, selection_key
from negopilot_agent.offer_proposal.generator import Candidate
from tests.test_offer_proposal import bench, FakeCaller, propose

TERMS={'unit_price':3900,'payment_days':30,'contract_days':365}
def facts(**kw):
    return TransactionFacts(committed_quantity=1000,**kw)
def test_opposing_price_cashflow_and_exact_financing():
    row=compare(TERMS,bench(supplier__payment_days=60),facts(buyer_financing_rate=.06,supplier_financing_rate=.12))
    assert row['purchase_amount']==3900000 and row['buyer_price_saving']==200000
    assert row['supplier_revenue_change']==-200000
    assert row['buyer_financing_cost']==19233
    assert row['supplier_financing_saving']==38466
    assert row['buyer_net_saving']==180767
    assert row['supplier_revenue_finance_change']==-161534

def test_missing_and_zero_rates_are_different():
    missing=compare(TERMS,bench(),facts())
    zero=compare(TERMS,bench(),facts(buyer_financing_rate=0,supplier_financing_rate=0))
    assert missing['buyer_net_saving'] is None and missing['supplier_financing_saving'] is None
    assert zero['buyer_financing_cost']==0 and zero['buyer_net_saving']==200000

def test_quantity_not_invented_from_annual_volume_or_duration():
    row=compare(TERMS,bench(),TransactionFacts())
    assert row['purchase_amount'] is None
    short=compare(TERMS,bench(),facts())
    long=compare(TERMS|{'contract_days':730},bench(),facts())
    assert short['purchase_amount']==long['purchase_amount']

def test_longer_payment_reverses_financing_effect():
    row=compare(TERMS|{'payment_days':60},bench(supplier__payment_days=30),facts(buyer_financing_rate=.06,supplier_financing_rate=.12))
    assert row['buyer_financing_cost']==-19233 and row['supplier_financing_saving']==-38466

@pytest.mark.parametrize('value',[-.1,1.1,float('inf'),float('nan'),'0.06'])
def test_rate_validation(value):
    with pytest.raises(ValidationError): facts(buyer_financing_rate=value)

def test_report_volume_assumption_checked_and_preserves_committed_quantity():
    f=facts(volume_option_quantity=1500,volume_option_unit_price=3800,buyer_financing_rate=.06,supplier_financing_rate=.12)
    out=propose(FakeCaller(bench()),transaction=f,target_price=3900,payment_days=45)
    report=out.offer.commercial_comparison
    assert report['selected']['quantity']==1000
    volume=next(r for r in report['rows'] if r['quantity']==1500)
    assert volume['purchase_amount']==5700000
    assert volume['quantity_change_vs_selected']==500
    assert volume['spend_change_vs_selected']==5700000-report['selected']['purchase_amount']
    assert any(a.get('quantity')==1500 and a['check_result'] for a in out.offer.assessed_alternatives)

def test_financing_tiebreak_prefers_later_same_price_when_costs_known():
    f=facts(buyer_financing_rate=.06,supplier_financing_rate=.12)
    out=propose(FakeCaller(bench(supplier__last_price=3900)),transaction=f,target_price=3900,payment_days=45)
    assert out.offer.payment_days==45
    assert '금융' in ' '.join(out.offer.commercial_comparison['selection_reasons'])


def test_explicit_payment_baseline_and_missing_baseline():
    b=bench(supplier__payment_days=None)
    missing=compare(TERMS,b,facts(buyer_financing_rate=.06))
    assert missing['buyer_financing_cost'] is None
    known=compare(TERMS,b,facts(previous_payment_days=60,buyer_financing_rate=.06))
    assert known['buyer_financing_cost']==19233


def test_priority_review_explains_burden_and_unverified_counterbenefits():
    out=propose(FakeCaller(bench(supplier__last_price=3800)),transaction=facts(previous_payment_days=60,buyer_financing_rate=.06),target_price=3900,payment_days=30)
    report=out.offer.commercial_comparison
    assert report['selected']['label']=='현재 후보 중 우선 검토안'
    reasons=' '.join(report['selection_reasons'])
    assert '구매자에게 불리한 점' in reasons and '반대급부' in reasons and '추가 확보 조건' in reasons


def test_missing_transaction_preserves_legacy_json_and_batch_columns():
    from negopilot_agent.offer_proposal import excel
    out = propose(FakeCaller(bench())).model_dump(mode='json')
    assert 'review_basis' not in out and 'draft_status' not in out
    assert not {'condition_basis', 'assessed_alternatives', 'commercial_comparison'} & out['offer'].keys()
    assert 'transaction' not in excel.FIELDS
    assert excel.header_field('transaction') is None
    assert out == propose(FakeCaller(bench()), transaction=None).model_dump(mode='json')
