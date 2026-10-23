"""법규 검토 화면에서 check_offer 결과에 거래 사실 판단을 덧붙인다.

거래 사실 판단(legal_assessment)은 사용자가 입력한 사실로 쟁점을 더 좁히는 보조 정보다.
business-mcp의 판정(지급 기한, 연동 누락, 예외 안내)은 지우지 않는다 (#257).
같은 쟁점의 MCP 위반이 이미 있으면 거래 사실 판단은 설명(info)으로만 붙여 위반이 두 번 세어지지 않게 한다.
"""
from contextlib import asynccontextmanager

from negopilot_shared.transaction import TransactionFacts

from negopilot_agent.legal_assessment import assess_transaction
from negopilot_agent.routing.mcp_client import McpToolCaller

SEVERITY = {"blocked": "violation", "needs_information": "warning", "supported": "info"}
BUCKET = {"violation": "violations", "warning": "warnings", "info": "info"}
VERDICT = {"VIOLATION": "위반 소지 있음", "CHECK_NEEDED": "확인 필요", "OK": "입력 사실에 따른 조건부 검토 완료"}
# 거래 사실 쟁점 → 같은 쟁점을 판정하는 MCP 항목
SAME_ISSUE = {
    "payment.deadline": {"PAYMENT_DAYS_OVER_60"},
    "linkage.agreement": {"LINKAGE_TERMS_MISSING", "ENERGY_LINKAGE_TERMS_MISSING"},
}


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
        if tool != 'check_offer' or not entered(self.offer.transaction):
            return result
        result = {**result, **{key: list(result.get(key, [])) for key in BUCKET.values()}}
        mcp_violations = {f['code'] for f in result['violations']}
        assessment = assess_transaction(self.offer.transaction, self.offer.model_dump(mode='json'), self.offer.as_of)
        for f in assessment['findings']:
            severity = SEVERITY[f['status']]
            if severity == 'violation' and SAME_ISSUE.get(f['id'], set()) & mcp_violations:
                severity = 'info'
            result[BUCKET[severity]].append(transaction_finding(f, severity))
        result['verdict_code'] = ('VIOLATION' if result['violations'] else
                                  'CHECK_NEEDED' if result['warnings'] else 'OK')
        result['verdict'] = VERDICT[result['verdict_code']]
        refs = [r for key in BUCKET.values() for f in result[key] for r in f.get('law_refs', [])]
        result['law_refs'] = list({(r.get('law'), r.get('article_no'), r.get('paragraph')): r for r in refs}.values())
        return result


# 화면 폼에 '모름' 선택지가 없어 비워 둬도 기본으로 보내는 값
FORM_DEFAULTS = {"payment_exception_claimed": False}


def entered(transaction: TransactionFacts | None) -> bool:
    """거래 사실을 하나라도 입력했는지. 화면은 비워 둬도 기본값을 채운 객체를 보내므로, 그때는 check_offer 결과를 그대로 둔다.
    구조화된 거래 사실 검토(legal_review)는 review_api가 따로 낸다."""
    if transaction is None:
        return False
    given = transaction.model_dump(exclude_defaults=True)
    return any(given[key] != FORM_DEFAULTS.get(key) for key in given)


def transaction_finding(f: dict, severity: str) -> dict:
    """거래 사실 쟁점 하나를 check_offer 검사 항목 형식으로 바꾼다."""
    refs = [{'law': 'subcontract_act', 'article_no': r['title'][r['title'].index('제'):]}
            for r in f.get('law_refs', []) if '제' in r.get('title', '')]
    message = f['summary']
    if f['missing_fields']:
        message += ' 미확인: ' + ', '.join(f['missing_fields'])
    for constraint in f['constraints']:
        if constraint.get('type') in ('latest_payment_date', 'proposed_payment_date'):
            message += f" {constraint['type']}: {constraint['value']}"
    return {'code': 'TRANSACTION_' + f['id'].replace('.', '_').upper(), 'severity': severity,
            'message': message, 'law_refs': refs}
