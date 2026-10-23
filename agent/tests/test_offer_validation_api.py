"""외부 문구 탐지와 저장 조건 및 가격점별 재검사."""

from uuid import uuid4

import httpx2 as httpx
import pytest
from fastmcp.exceptions import ToolError

from negopilot_agent.api.app import create_app
from negopilot_agent.api.settings import ApiSettings
from negopilot_agent.offer_proposal.service import build_package
from tests.test_offer_proposal import FakeCaller, bench, request

KEY = {'X-Internal-Api-Key': 'validation-test'}


async def post(path, body, caller=None, headers=KEY):
    app = create_app(ApiSettings(internal_api_key='validation-test'), generation_caller=caller)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        return await client.post(path, json=body, headers=headers)


async def text_body(texts):
    pkg = await build_package(request(), FakeCaller(bench()))
    return {'request_id': str(uuid4()), 'document': pkg.document.model_dump(mode='json'), 'texts': texts}


async def test_texts_detect_internal_terms_and_unknown_numbers_without_mcp():
    caller = FakeCaller(bench())
    body = await text_body({'cover_letter': '최대 수용가 8765원', 'mail_message': '2050원', 'empty': ''})
    body['document']['content_hash'] = 'ignored-invalid-hash'
    response = await post('/internal/offer-texts/check', body, caller)
    assert response.status_code == 200
    data = response.json()
    assert data['checked'] is True and data['request_id'] == body['request_id']
    assert {'field': 'cover_letter', 'code': 'INTERNAL_TERM', 'detail': '최대 수용가'} in data['findings']
    assert {'field': 'mail_message', 'code': 'UNKNOWN_NUMBER', 'detail': '2050'} in data['findings']
    assert not any(f['field'] == 'empty' for f in data['findings'])
    assert data['method'] and data['limitations']
    assert caller.calls == []


async def test_public_numbers_ratios_and_date_fragments_are_allowed():
    body = await text_body({})
    doc = body['document']
    body['texts'] = {'linkage_terms.조정 산식': f"{doc['unit_price']:,}원 {doc['payment_days']}일 40%",
                     'title': f"{doc['reply_by']} {doc['issued_on']} {doc['as_of']} 2026년 10월 23일"}
    response = await post('/internal/offer-texts/check', body)
    assert response.status_code == 200
    assert response.json()['findings'] == []


@pytest.mark.parametrize('texts', [{'x': '가' * 2001}, {str(i): '' for i in range(21)}])
async def test_text_limits(texts):
    response = await post('/internal/offer-texts/check', await text_body(texts))
    assert response.status_code == 400


def validation_body(**changes):
    return {'request_id': str(uuid4()), 'offer': {'supplier': 'SUP-0007', 'rfq_id': 'RFQ-2026-001',
        'as_of': '2026-10-23', 'unit_price': 1948, 'payment_days': 60, 'price_linkage': True,
        'contract_days': 365, 'raw_material_ratio': 0.42, 'energy_ratio': None,
        'linkage_waived_by_agreement': False, 'supplier_size': {}, 'buyer_size': {}, **changes},
        'price_points': [{'name': 'settlement', 'unit_price': 1999}, {'name': 'walk_away', 'unit_price': 2050}]}


async def test_validate_calls_each_price_once_preserving_all_other_terms():
    caller = FakeCaller(bench())
    body = validation_body()
    response = await post('/internal/offer-proposals/validate', body, caller)
    assert response.status_code == 200
    data = response.json()
    checks = [args for _, tool, args in caller.calls if tool == 'check_offer']
    assert [a['unit_price'] for a in checks] == [1948, 1999, 2050]
    assert all({k: v for k, v in a.items() if k != 'unit_price'} ==
               {k: v for k, v in checks[0].items() if k != 'unit_price'} for a in checks)
    assert len([1 for _, tool, _ in caller.calls if tool == 'get_price_benchmarks']) == 1
    assert caller.sessions == 1
    assert data['conditions']['contract_amount'] == 1948 * 120_000
    assert data['price_points'][0]['vs_target_pct'] == round((1999 / 3800 - 1) * 100, 1)
    assert data['scope'] and data['limitations']


async def test_violations_are_results_and_conditions_are_not_repaired():
    caller = FakeCaller(bench())
    response = await post('/internal/offer-proposals/validate', validation_body(payment_days=90), caller)
    assert response.status_code == 200
    assert response.json()['conditions']['verdict_code'] == 'VIOLATION'
    assert all(p['violations'] for p in response.json()['price_points'])
    assert all(args['payment_days'] == 90 for _, tool, args in caller.calls if tool == 'check_offer')


@pytest.mark.parametrize('code', ['RFQ_NOT_FOUND', 'SUPPLIER_NOT_FOUND', 'VALUE_OUT_OF_RANGE', 'DB_UNAVAILABLE'])
async def test_check_tool_errors(code):
    class Rejected(FakeCaller):
        async def __call__(self, server, tool, args):
            raise ToolError(f'[{code}] missing')
    response = await post('/internal/offer-proposals/validate', validation_body(), Rejected(bench()))
    assert response.status_code == 422
    assert response.json()['error']['code'] == code


async def test_transaction_payment_deadline_is_preserved_for_each_price():
    from tests.test_review_proposal_flow import offer

    data = offer()
    data['transaction'].update(receipt_date='2026-10-01', upstream_payment_applicable=True,
                               upstream_received_on='2026-10-01')
    caller = FakeCaller(bench())
    body = validation_body() | {'offer': data}
    response = await post('/internal/offer-proposals/validate', body, caller)
    assert response.status_code == 200
    results = [response.json()['conditions'], *response.json()['price_points']]
    assert all(result['verdict_code'] == 'VIOLATION' and result['violations'] for result in results)
    assert len([1 for _, tool, _ in caller.calls if tool == 'check_offer']) == 3


@pytest.mark.parametrize('path', ['/internal/offer-texts/check', '/internal/offer-proposals/validate'])
async def test_authentication_required(path):
    caller = FakeCaller(bench())
    body = await text_body({}) if path.endswith('check') else validation_body()
    assert (await post(path, body, caller, headers={})).status_code == 401
    assert caller.calls == []
