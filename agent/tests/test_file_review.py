"""파일 검토 에이전트: 판정은 규칙 값 그대로, 수정안은 재검사를 통과한 것만 남는다."""
import json
from types import SimpleNamespace as NS
from uuid import uuid4

import httpx2 as httpx

from negopilot_agent.api.app import create_app
from negopilot_agent.api.settings import ApiSettings
from negopilot_agent.file_review import FileReviewAgent


def offer(supplier, price, pay=60, link=True):
    return dict(supplier=supplier, rfq_id='RFQ-2026-004', unit_price=price, payment_days=pay, price_linkage=link,
                raw_material_ratio=.4, contract_days=365, supplier_size={}, buyer_size={}, as_of='2026-10-23')


LAST = {'SUP-0001': 1000, 'SUP-0002': 2000, 'SUP-0003': 3000}


class FakeCaller:
    def __init__(self):
        self.calls = []

    async def __call__(self, server, tool, args):
        self.calls.append((tool, args))
        if tool == 'check_offer':
            if args['supplier'] == 'SUP-9999':
                raise RuntimeError('[SUPPLIER_NOT_FOUND] 활성 협력사가 없다')
            violations = [{'code': 'PAYMENT_DAYS_OVER_60', 'severity': 'violation', 'message': '60일 초과', 'law_refs': []}] \
                if args['payment_days'] > 60 else []
            return {'verdict_code': 'VIOLATION' if violations else 'OK', 'violations': violations, 'warnings': [], 'info': [],
                    'supplier': {'supplier_id': args['supplier']}, 'rfq': {'process': '단조', 'material': 'SCM440'}}
        if tool == 'list_supplier_quotes':
            return {'items': [{'unit_price': LAST[args['supplier_id']]}]}
        if tool == 'search_regulation':
            return [{'citation': '하도급법 제4조②', 'text': '일률적인 비율로 단가를 인하'}]
        raise AssertionError(tool)


def call(name, args, i=0):
    return NS(id=f'c{i}', function=NS(name=name, arguments=json.dumps(args, ensure_ascii=False)),
              model_dump=lambda: {'id': f'c{i}', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}})


class FakeClient:
    """정해진 순서로 도구를 부르는 가짜 모델."""
    def __init__(self, script):
        self.script, self.seen = list(script), []
        self.chat = NS(completions=NS(create=self.create))

    async def create(self, **kw):
        self.seen.append(list(kw['messages']))
        calls = self.script.pop(0)
        return NS(choices=[NS(message=NS(content=None, tool_calls=calls))],
                  usage=NS(prompt_tokens=10, completion_tokens=5))


SUBMIT = {'summary': '세 협력사에 같은 비율 인하', 'questions': [{'row_ids': [1, 2, 99], 'question': '협력사별 인하 사유는?'}],
          'row_notes': [{'row_id': 3, 'note': '지급기한 초과'}, {'row_id': 42, 'note': '없는 행'}],
          'file_issues': [{'kind': 'uniform_cut', 'row_ids': [1, 2], 'summary': '일률 인하 소지',
                           'law_refs': ['하도급법 제4조②', '하도급법 제13조']}]}


async def test_rule_verdict_kept_and_only_verified_revision_survives():
    caller = FakeCaller()
    client = FakeClient([
        [call('compare_rows', {})],
        [call('recheck_offer', {'row_id': 3, 'changes': {'payment_days': 75}, 'reason': '그대로'}, 1),
         call('recheck_offer', {'row_id': 3, 'changes': {'payment_days': 60, 'supplier': 'X'}, 'reason': '60일'}, 2)],
        [call('search_law', {'query': '일률적 인하'}, 3), call('get_row', {'row_id': 7}, 4)],
        [call('submit_review', SUBMIT, 5)],
    ])
    offers = [offer('SUP-0001', 960), offer('SUP-0002', 1920), offer('SUP-0003', 3000, pay=90), offer('SUP-9999', 1)]
    result = await FileReviewAgent(caller, client=client).run(offers, lambda o: caller)
    rows = {r['row_id']: r for r in result['rows']}
    assert [rows[i]['verdict_code'] for i in (1, 2, 3, 4)] == ['OK', 'OK', 'VIOLATION', 'ERROR']
    assert rows[4]['error'] == 'SUPPLIER_NOT_FOUND'
    # compare_rows가 같은 변화율(-4%) 묶음을 계산해 모델에게 준다
    compare = json.loads(client.seen[1][-1]['content'])
    assert compare['same_cut_groups'] == [{'change_pct': -4.0, 'row_ids': [1, 2]}]
    assert [(c['kind'], c['row_ids']) for c in compare['pattern_candidates']] == [('uniform_cut', [1, 2])]
    # 75일 수정안은 여전히 위반이라 버리고, 바꿀 수 없는 항목(supplier)은 무시한 60일 수정안만 남는다
    assert rows[3]['revision']['changes'] == {'payment_days': 60}
    assert rows[3]['revision']['recheck_verdict'] == 'OK'
    assert rows[1]['revision'] is None
    # 없는 행 번호는 지운다
    assert result['questions'][0]['row_ids'] == [1, 2]
    assert rows[1]['file_issues'][0]['kind'] == 'uniform_cut' and rows[3]['file_issues'] == []
    assert rows[3]['notes'] == ['지급기한 초과']
    # 행별 규칙 안내는 모델과 상관없이 붙는다
    assert rows[3]['messages'] == ['[위반 소지] 60일 초과'] and rows[1]['messages'] == []
    # 도구 결과에 없던 조문(제13조)은 지운다
    assert result['file_issues'][0]['law_refs'] == ['하도급법 제4조②']
    assert result['usage']['model_calls'] == 4 and result['completed']
    assert any(t['tool'] == 'get_row' and not t['ok'] for t in result['trace'])


async def test_forces_submit_on_last_step():
    caller = FakeCaller()
    client = FakeClient([[call('compare_rows', {})], [call('submit_review', SUBMIT, 1)]])
    result = await FileReviewAgent(caller, client=client, max_steps=2).run([offer('SUP-0001', 960)], lambda o: caller)
    assert result['completed']
    assert result['rows'][0]['verdict_code'] == 'OK'


async def test_file_endpoint_uses_injected_client():
    caller = FakeCaller()
    client = FakeClient([[call('submit_review', SUBMIT)]])
    app = create_app(ApiSettings(internal_api_key='k'), generation_caller=caller)
    # create_app는 클라이언트를 받지 않으므로 라우트를 다시 붙인다
    from negopilot_agent.api.review_api import add_review_routes
    from negopilot_agent.api.app import error_response
    app.router.routes = [r for r in app.router.routes if getattr(r, 'path', '') != '/internal/legal-reviews/file']
    add_review_routes(app, ApiSettings(internal_api_key='k'), lambda: None, error_response, caller, client)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://t') as c:
        r = await c.post('/internal/legal-reviews/file', json={'request_id': str(uuid4()),
                         'offers': [offer('SUP-0001', 960), offer('SUP-0002', 1920)]})
    assert r.status_code == 200, r.text
    assert [row['verdict_code'] for row in r.json()['rows']] == ['OK', 'OK']


async def test_same_supplier_repeat_candidate():
    caller = FakeCaller()
    client = FakeClient([[call('compare_rows', {})], [call('submit_review', SUBMIT, 1)]])
    offers = [offer('SUP-0003', 3000, pay=75), offer('SUP-0003', 3000, pay=90), offer('SUP-0001', 1000)]
    await FileReviewAgent(caller, client=client).run(offers, lambda o: caller)
    compare = json.loads(client.seen[1][-1]['content'])
    assert [(c['kind'], c['row_ids']) for c in compare['pattern_candidates']] == [('same_supplier_repeat', [1, 2])]


def test_guard_summary_drops_conclusions():
    from negopilot_agent.file_review.agent import guard_summary, CAUTION
    out = guard_summary('5개 행에 같은 비율 인하가 있습니다. 위반 소지는 낮아 보입니다.\n지급기한은 적법 범위 내에 있습니다.')
    assert out == '5개 행에 같은 비율 인하가 있습니다. ' + CAUTION
    assert guard_summary('확인 필요 행이 2개입니다.') == '확인 필요 행이 2개입니다. ' + CAUTION
    # 지우고 나서 내용이 거의 없으면 파일 쟁점 요약으로 채운다
    assert guard_summary('위반 소지는 발견되지 않았습니다. 최종 판단은 법무 검토가 필요합니다.',
                         [{'summary': '5개 협력사 -4.0% 같은 비율 인하.'}]) == '5개 협력사 -4.0% 같은 비율 인하. ' + CAUTION


async def test_uniform_cut_always_asks_per_supplier_reason():
    caller = FakeCaller()
    submit = {**SUBMIT, 'questions': []}
    client = FakeClient([[call('submit_review', submit)]])
    result = await FileReviewAgent(caller, client=client).run([offer('SUP-0001', 960), offer('SUP-0002', 1920)],
                                                              lambda o: caller)
    assert result['questions'] and result['questions'][0]['row_ids'] == [1, 2]
    assert '협력사별' in result['rows'][0]['questions'][0]
