"""Feed transaction findings into the same evidence pipeline used for the answer."""
from contextlib import asynccontextmanager
from negopilot_agent.legal_assessment import assess_transaction
from negopilot_agent.routing.mcp_client import McpToolCaller

class ReviewedToolCaller:
    def __init__(self, offer, caller=None):
        self.offer, self.caller = offer, caller or McpToolCaller()

    @asynccontextmanager
    async def session(self):
        session = getattr(self.caller, 'session', None)
        if session:
            async with session():
                yield
        else:
            yield

    async def __call__(self, server, tool, args):
        result = await self.caller(server, tool, args)
        if tool != 'check_offer' or self.offer.transaction is None:
            return result
        result = dict(result)
        assessment = assess_transaction(self.offer.transaction, self.offer.model_dump(mode='json'), self.offer.as_of)
        # Legacy boolean/estimated-amount checks cannot certify a fully specified agreement.
        for severity in ('violations', 'warnings', 'info'):
            result[severity] = [f for f in result.get(severity, [])
                               if not any(x in f['code'] for x in ('LINKAGE_', 'PAYMENT_DAYS_', 'ENERGY_RATIO_'))]
        for f in assessment['findings']:
            severity = {'blocked': 'violation', 'needs_information': 'warning', 'supported': 'info'}[f['status']]
            refs = []
            for r in f.get('law_refs', []):
                title = r.get('title', '')
                if '제' in title:
                    refs.append({'law': 'subcontract_act', 'article_no': title[title.index('제'):]})
            message = f['summary']
            if f['missing_fields']:
                message += ' 미확인: ' + ', '.join(f['missing_fields'])
            for constraint in f['constraints']:
                if constraint.get('type') in ('latest_payment_date', 'proposed_payment_date'):
                    message += f" {constraint['type']}: {constraint['value']}"
            result[{'violation': 'violations', 'warning': 'warnings', 'info': 'info'}[severity]].append(
                {'code': 'TRANSACTION_' + f['id'].replace('.', '_').upper(), 'severity': severity,
                 'message': message, 'law_refs': refs})
        result['verdict_code'] = 'VIOLATION' if result['violations'] else 'CHECK_NEEDED' if result['warnings'] else 'OK'
        result['verdict'] = {'VIOLATION': '위반 소지 있음', 'CHECK_NEEDED': '확인 필요', 'OK': '입력 사실에 따른 조건부 검토 완료'}[result['verdict_code']]
        result['law_refs'] = [r for key in ('violations','warnings','info') for f in result[key] for r in f.get('law_refs', [])]
        return result
