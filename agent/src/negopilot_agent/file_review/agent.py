"""엑셀 파일 하나(여러 거래 행)를 검토하는 에이전트 (08-2).

판정은 규칙, 일은 에이전트:
- 행마다 판정(verdict_code)은 business-mcp check_offer(+ 거래 사실 판단)가 낸 값을 그대로 쓴다. 모델은 바꾸지 못한다.
- 모델(gpt-4.1-mini)은 도구를 골라 부르며 파일 전체를 본다. 행 간 패턴(같은 비율 인하, 같은 협력사 반복)을 찾고,
  사유 문장을 조문과 비교하고, 빠진 정보를 묶어 물을 질문을 만들고, 위반 행의 수정안을 만든다.
- 수정안은 recheck_offer 도구로 check_offer를 다시 통과한 것만 결과에 남긴다. 검증 안 된 수정안은 버린다.
- 도구는 모두 코드(규칙·DB 조회·조문 검색)다. 숫자 계산은 모델이 하지 않는다.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from negopilot_agent import timing, tracing

log = logging.getLogger(__name__)

DEFAULT_MODEL = 'gpt-4.1-mini'
MAX_STEPS = 12            # 도구 호출 왕복 상한. 넘으면 그때까지 모은 결과로 끝낸다
MAX_RECHECKS_PER_ROW = 3
EDITABLE = {'unit_price', 'payment_days', 'price_linkage', 'contract_days', 'linkage_waived_by_agreement'}
CHECK_ARGS = ('supplier', 'rfq_id', 'unit_price', 'payment_days', 'price_linkage', 'raw_material_ratio', 'contract_days',
              'linkage_waived_by_agreement', 'supplier_size', 'buyer_size', 'energy_ratio', 'as_of')

SYSTEM = """너는 1차 협력사 구매 담당자를 돕는 하도급 거래조건 검토 에이전트다. 엑셀 파일 하나에 든 거래 행 전체를 검토한다.

규칙:
- 각 행의 판정(위반 소지/확인 필요/문제 없음)은 이미 규칙 검사(check_offer)가 냈다. 판정을 바꾸거나 새로 매기지 않는다.
- 숫자는 도구 결과만 쓴다. 직접 계산하거나 지어내지 않는다.
- 법 해석의 최종 판단은 사람(법무)이 한다. 단정하지 말고 "소지", "확인 필요"로 쓴다.
  "위반 소지가 낮다/없다", "적법하다" 같은 결론은 요약에도 쓰지 않는다.
- 조문 번호는 search_law 결과나 get_row의 근거 조문에서 본 것만 쓴다. 기억으로 조문 번호를 적지 않는다.

할 일 (필요한 도구를 골라서 부른다):
1. compare_rows로 행들을 나란히 보고, 행 하나만 봐서는 안 보이는 패턴을 찾는다.
   예: 여러 협력사에 같은 비율로 단가를 내림(하도급법 제4조② 일률적 인하 소지), 같은 협력사에 여러 RFQ에서 같은 불리한 조건 반복.
   비율이 제각각이면 패턴이 아니다. 근거 조문이 필요하면 search_law로 찾는다.
   compare_rows의 pattern_candidates는 코드가 찾은 후보다. 맞으면 같은 kind로 file_issues에 올리고, 아니면 버린다.
   같은 협력사 반복은 kind=same_supplier_repeat로 쓴다.
2. 위반 소지 행은 recheck_offer로 수정안을 검사한다. 단가는 가능하면 그대로 두고 위반을 만든 항목만 최소로 바꾼다
   (예: 지급기한 60일 이내, 연동 약정 포함). 위반이 남으면 결과 코드를 보고 다시 고친다.
3. 사유 문장(지급 예외 사유, 감액 사유, 미연동 합의 사유)이 있으면 search_law로 관련 조문을 찾아 근거가 충분한지 보고,
   부족하면 무엇을 더 받아야 하는지 적는다.
4. 판정을 바꿀 수 있는 빠진 정보(구매자·공급자 규모, 원재료 비중, 감액 사유와 증빙, 예외 증빙 등)는 질문으로 묶는다.
   여러 행에 같은 질문이면 하나로 묶고 해당 행을 모두 적는다. 단, 묶더라도 무엇을 받아야 하는지는 항목별로 구체적으로 쓴다.
   예: 일률 인하 묶음이면 "협력사별 인하 사유와 산출 근거", 감액 행이면 "감액 사유와 증빙 자료",
   원가 반영 행이면 "원가 산정 근거 자료", 지급 예외 행이면 "예외 사유의 증빙 자료".
   행별 규칙 안내(check_offer 메시지)는 결과에 그대로 붙으므로 같은 말을 되풀이하지 말고, 그 위에 더 물을 것을 적는다.
마지막에 submit_review를 한 번 부른다. 문장은 한국어로 짧게 쓴다."""

TOOLS = [
    {'type': 'function', 'function': {
        'name': 'compare_rows',
        'description': '모든 행을 표로 돌려준다: 협력사, RFQ, 제안 단가, 직전 견적, 직전 견적 대비 변화율(%), 지급기한, 연동, 판정, 검사 코드. '
                       '같은 변화율 행 묶음과 같은 협력사 행 묶음도 계산해 준다.',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}},
    {'type': 'function', 'function': {
        'name': 'get_row',
        'description': '행 하나의 입력값, 거래 사실(사유 문장 포함), 규칙 검사 항목 전체(메시지·근거 조문)를 돌려준다.',
        'parameters': {'type': 'object', 'properties': {'row_id': {'type': 'integer'}},
                       'required': ['row_id'], 'additionalProperties': False}}},
    {'type': 'function', 'function': {
        'name': 'search_law',
        'description': '하도급법·시행령·심사지침에서 관련 조문을 찾는다. 법령 용어로 물으면 정확하다.',
        'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}},
                       'required': ['query'], 'additionalProperties': False}}},
    {'type': 'function', 'function': {
        'name': 'recheck_offer',
        'description': '행의 조건 일부를 바꿔 check_offer로 다시 검사한다. 바꿀 수 있는 항목: unit_price, payment_days, '
                       'price_linkage, contract_days, linkage_waived_by_agreement. 결과 판정과 남은 코드를 돌려준다.',
        'parameters': {'type': 'object', 'properties': {
            'row_id': {'type': 'integer'},
            'changes': {'type': 'object', 'properties': {
                'unit_price': {'type': 'integer'}, 'payment_days': {'type': 'integer'},
                'price_linkage': {'type': 'boolean'}, 'contract_days': {'type': 'integer'},
                'linkage_waived_by_agreement': {'type': 'boolean'}}, 'additionalProperties': False},
            'reason': {'type': 'string', 'description': '왜 이렇게 바꾸는지 한 문장'}},
            'required': ['row_id', 'changes', 'reason'], 'additionalProperties': False}}},
    {'type': 'function', 'function': {
        'name': 'submit_review',
        'description': '검토 결과를 낸다. 마지막에 한 번만 부른다.',
        'parameters': {'type': 'object', 'properties': {
            'summary': {'type': 'string', 'description': '파일 전체 요약 2~3문장'},
            'file_issues': {'type': 'array', 'items': {'type': 'object', 'properties': {
                'kind': {'type': 'string', 'enum': ['uniform_cut', 'same_supplier_repeat', 'other']},
                'row_ids': {'type': 'array', 'items': {'type': 'integer'}},
                'summary': {'type': 'string'},
                'law_refs': {'type': 'array', 'items': {'type': 'string'}}},
                'required': ['kind', 'row_ids', 'summary', 'law_refs'], 'additionalProperties': False}},
            'row_notes': {'type': 'array', 'items': {'type': 'object', 'properties': {
                'row_id': {'type': 'integer'}, 'note': {'type': 'string'}},
                'required': ['row_id', 'note'], 'additionalProperties': False}},
            'questions': {'type': 'array', 'items': {'type': 'object', 'properties': {
                'row_ids': {'type': 'array', 'items': {'type': 'integer'}},
                'question': {'type': 'string'}},
                'required': ['row_ids', 'question'], 'additionalProperties': False}}},
            'required': ['summary', 'file_issues', 'row_notes', 'questions'], 'additionalProperties': False}}},
]


@dataclass
class Row:
    row_id: int
    offer: dict
    result: dict | None = None      # check_offer 결과 (거래 사실 판단 포함)
    error: str | None = None        # 입력 오류 코드 ([SUPPLIER_NOT_FOUND] 등)
    last_quote: int | None = None
    revisions: list[dict] = field(default_factory=list)   # recheck_offer가 검사한 수정안 전부

    @property
    def verdict_code(self) -> str:
        return self.result['verdict_code'] if self.result else 'ERROR'

    def codes(self) -> list[str]:
        if not self.result:
            return [self.error or 'ERROR']
        return [f['code'] for k in ('violations', 'warnings', 'info') for f in self.result.get(k, [])]


LABELS = {'violations': '위반 소지', 'warnings': '확인 필요', 'info': '참고'}
ARTICLE = re.compile(r'제\d+조(의\d+)?')
# 요약에서 지우는 단정 문장 (최종 판단은 법무가 한다)
CONCLUSIVE = re.compile(r'적법|위반(이)? 아님|위반이 아니|문제(가)? 없|위반 소지(는|가)? (낮|없)|발견되지 않|해당하지 않는다')
CAUTION = '최종 판단은 법무 검토가 필요합니다.'
UNIFORM_CUT_QUESTION = '협력사별 인하 사유와 산출 근거 자료를 받아 주세요. 같은 비율 인하의 정당한 사유가 있는지 확인해야 합니다.'


def guard_summary(text: str, issues: list[dict] = ()) -> str:
    """모델 요약에서 단정 문장을 빼고 주의 문장을 붙인다. 남은 내용이 없으면 파일 쟁점 요약으로 채운다."""
    sentences = [x for x in re.split(r'(?<=[.!?。])\s+|\n+', text or '') if x.strip()]
    kept = [x for x in sentences if not CONCLUSIVE.search(x) and x != CAUTION]
    if len(kept) < len(sentences) - sentences.count(CAUTION) and len(' '.join(kept)) < 40:
        kept = [i['summary'] for i in issues if i.get('summary')] + kept
    return ' '.join(kept + [CAUTION])


def rule_messages(row: Row) -> list[str]:
    """행별 규칙 안내. 화면에 행 검토 결과로 그대로 보인다 (위반 → 확인 필요 → 참고 순)."""
    return [f"[{label}] {f['message']}" for k, label in LABELS.items() for f in (row.result or {}).get(k, [])]


def check_args(offer: dict) -> dict:
    return {k: offer[k] for k in CHECK_ARGS if offer.get(k) is not None}


def error_code(error: Exception) -> str:
    text = ' '.join(str(a) for a in getattr(error, 'args', ())) or str(error)
    if text.startswith('[') and ']' in text:
        return text[1:text.index(']')]
    return type(error).__name__


class FileReviewAgent:
    def __init__(self, caller, client=None, model: str | None = None, max_steps: int = MAX_STEPS):
        """caller: (server, tool, args) → 결과. 행 검사에는 거래 사실 판단을 덧붙이는 ReviewedToolCaller를 행마다 쓴다.
        client: openai.AsyncOpenAI 호환 (chat.completions.create). 없으면 첫 호출 때 만든다."""
        self.caller, self._client = caller, client
        self.model = model or os.getenv('NEGOPILOT_FILE_REVIEW_MODEL', DEFAULT_MODEL)
        self.max_steps = max_steps
        self.trace: list[dict] = []
        self.usage = {'input_tokens': 0, 'output_tokens': 0, 'model_calls': 0}
        self.seen_citations: set[str] = set()   # 도구 결과로 본 조문. 결과의 law_refs는 여기 있는 것만 남긴다

    @property
    def client(self):
        if self._client is None:
            import openai
            self._client = tracing.wrap_llm_client(openai.AsyncOpenAI(max_retries=1))
        return self._client

    # ---------- 규칙 검사 (모델 없이) ----------

    async def check(self, row: Row, row_caller) -> None:
        try:
            row.result = await row_caller('business-mcp', 'check_offer', check_args(row.offer))
        except Exception as e:  # noqa: BLE001  입력 오류는 행 오류로 남기고 다른 행은 계속 본다
            row.error = error_code(e)
            return
        rfq = row.result.get('rfq') or {}
        try:
            page = await self.caller('business-mcp', 'list_supplier_quotes', {
                'supplier_id': row.result['supplier']['supplier_id'], 'process': rfq.get('process'),
                'material': rfq.get('material'), 'size': 1})
            items = (page or {}).get('items') or (page or {}).get('quotes') or []
            row.last_quote = items[0]['unit_price'] if items else None
        except Exception as e:  # noqa: BLE001  견적 이력이 없어도 검토는 계속한다
            log.warning('견적 이력 조회 실패: %s', type(e).__name__)

    # ---------- 도구 ----------

    def compare_rows(self, rows: dict[int, Row]) -> dict:
        table, by_change, by_supplier = [], {}, {}
        for r in rows.values():
            price = r.offer['unit_price']
            change = round((price / r.last_quote - 1) * 100, 1) if r.last_quote else None
            table.append({'row_id': r.row_id, 'supplier': r.offer['supplier'], 'rfq_id': r.offer['rfq_id'],
                          'unit_price': price, 'last_quote': r.last_quote, 'change_pct': change,
                          'payment_days': r.offer['payment_days'], 'price_linkage': r.offer['price_linkage'],
                          'verdict': r.verdict_code, 'codes': r.codes()})
            if change is not None and change < 0:
                by_change.setdefault(change, []).append(r.row_id)
            by_supplier.setdefault(r.offer['supplier'], []).append(r.row_id)
        same_cut = [{'change_pct': k, 'row_ids': v} for k, v in by_change.items() if len(v) >= 2]
        same_supplier = [{'supplier': k, 'row_ids': v} for k, v in by_supplier.items() if len(v) >= 2]
        candidates = [{'kind': 'uniform_cut', 'row_ids': g['row_ids'],
                       'basis': f"직전 견적 대비 {g['change_pct']}% 같은 비율 인하 {len(g['row_ids'])}건"}
                      for g in same_cut if len({rows[i].offer['supplier'] for i in g['row_ids']}) >= 2]
        for g in same_supplier:
            # 같은 협력사 여러 행에 공통으로 걸린 위반·확인 코드가 있으면 반복 후보로 둔다
            shared = set.intersection(*({f['code'] for k in ('violations', 'warnings') for f in (rows[i].result or {}).get(k, [])}
                                        for i in g['row_ids']))
            if shared:
                candidates.append({'kind': 'same_supplier_repeat', 'row_ids': g['row_ids'],
                                   'basis': f"{g['supplier']} 여러 행에 같은 코드 반복: {', '.join(sorted(shared))}"})
        return {'rows': table, 'same_cut_groups': same_cut, 'same_supplier_groups': same_supplier,
                'pattern_candidates': candidates}

    def get_row(self, row: Row) -> dict:
        out = {'row_id': row.row_id, 'offer': row.offer, 'verdict': row.verdict_code, 'last_quote': row.last_quote}
        if row.error:
            out['error'] = row.error
        if row.result:
            out['findings'] = [{'code': f['code'], 'severity': f['severity'], 'message': f['message'],
                                'law_refs': f.get('law_refs', [])}
                               for k in ('violations', 'warnings', 'info') for f in row.result.get(k, [])]
        return out

    async def search_law(self, query: str, as_of: str | None) -> list[dict]:
        hits = await self.caller('law-mcp', 'search_regulation', {'query': query, 'as_of': as_of, 'top_k': 3})
        out = [{'citation': h.get('citation') or h.get('label'), 'text': (h.get('text') or '')[:400]}
               for h in (hits or [])][:5]
        self.seen_citations.update(str(h['citation']) for h in out if h['citation'])
        return out

    async def recheck(self, row: Row, changes: dict, reason: str, row_caller) -> dict:
        changes = {k: v for k, v in changes.items() if k in EDITABLE}
        if row.result is None:
            return {'error': '입력 오류 행은 수정안을 검사할 수 없다'}
        if not changes:
            return {'error': '바꿀 항목이 없다'}
        if len(row.revisions) >= MAX_RECHECKS_PER_ROW:
            return {'error': f'이 행은 이미 {MAX_RECHECKS_PER_ROW}번 다시 검사했다'}
        offer = {**row.offer, **changes}
        try:
            result = await row_caller('business-mcp', 'check_offer', check_args(offer))
        except Exception as e:  # noqa: BLE001
            return {'error': error_code(e)}
        codes = [f['code'] for k in ('violations', 'warnings') for f in result.get(k, [])]
        row.revisions.append({'changes': changes, 'reason': reason, 'offer': offer,
                              'recheck_verdict': result['verdict_code'], 'recheck_codes': codes})
        return {'verdict': result['verdict_code'], 'remaining': codes}

    # ---------- 루프 ----------

    async def run(self, offers: list[dict], row_caller_for) -> dict:
        """offers: ReviewOffer dict 목록. row_caller_for(offer) → 그 행의 거래 사실을 덧붙이는 ToolCaller."""
        rows = {i: Row(i, o) for i, o in enumerate(offers, 1)}
        callers = {i: row_caller_for(o) for i, o in enumerate(offers, 1)}
        for i, r in rows.items():
            await self.check(r, callers[i])
        as_of = offers[0].get('as_of') if offers else None
        self.seen_citations.update(str(ref) for r in rows.values() for k in LABELS
                                   for f in (r.result or {}).get(k, []) for ref in f.get('law_refs', []))
        overview = [{'row_id': r.row_id, 'supplier': r.offer['supplier'], 'rfq_id': r.offer['rfq_id'],
                     'verdict': r.verdict_code, 'codes': r.codes()} for r in rows.values()]
        messages = [{'role': 'system', 'content': SYSTEM},
                    {'role': 'user', 'content': '파일의 행과 규칙 검사 결과:\n' + json.dumps(overview, ensure_ascii=False)}]
        submitted = None
        for step in range(self.max_steps):
            final_turn = step == self.max_steps - 1
            with timing.span('llm', self.model) as meta:
                response = await self.client.chat.completions.create(
                    model=self.model, messages=messages, temperature=0, tools=TOOLS,
                    tool_choice={'type': 'function', 'function': {'name': 'submit_review'}} if final_turn else 'auto')
                if usage := getattr(response, 'usage', None):
                    meta.update(input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens)
                    self.usage['input_tokens'] += usage.prompt_tokens
                    self.usage['output_tokens'] += usage.completion_tokens
            self.usage['model_calls'] += 1
            message = response.choices[0].message
            calls = message.tool_calls or []
            messages.append({'role': 'assistant', 'content': message.content,
                             'tool_calls': [c.model_dump() if hasattr(c, 'model_dump') else c for c in calls] or None})
            if not calls:   # 도구 없이 끝내려 하면 submit_review를 부르게 한다
                messages[-1].pop('tool_calls')
                messages.append({'role': 'user', 'content': 'submit_review로 결과를 내라.'})
                continue
            for call in calls:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments or '{}')
                except json.JSONDecodeError:
                    args = {}
                output = await self.dispatch(name, args, rows, callers, as_of)
                self.trace.append({'step': step, 'tool': name, 'args': args,
                                   'ok': not (isinstance(output, dict) and 'error' in output)})
                if name == 'submit_review':
                    submitted = args
                messages.append({'role': 'tool', 'tool_call_id': call.id,
                                 'content': json.dumps(output, ensure_ascii=False, default=str)[:6000]})
            if submitted is not None:
                break
        return self.result(rows, submitted)

    async def dispatch(self, name: str, args: dict, rows: dict[int, Row], callers, as_of) -> Any:
        try:
            if name == 'compare_rows':
                return self.compare_rows(rows)
            if name == 'submit_review':
                return {'ok': True}
            row_id = args.get('row_id')
            if name in ('get_row', 'recheck_offer') and row_id not in rows:
                return {'error': f'없는 행 {row_id}'}
            if name == 'get_row':
                return self.get_row(rows[row_id])
            if name == 'search_law':
                return await self.search_law(str(args.get('query', '')), as_of)
            if name == 'recheck_offer':
                return await self.recheck(rows[row_id], args.get('changes') or {}, str(args.get('reason', '')),
                                          callers[row_id])
        except Exception as e:  # noqa: BLE001  도구 실패는 모델에게 알리고 계속한다
            return {'error': type(e).__name__}
        return {'error': f'없는 도구 {name}'}

    def cited(self, ref: str) -> bool:
        """모델이 적은 조문이 도구 결과에 나온 조문과 같은 조 번호를 갖는지 본다 (기억으로 적은 조문 거르기)."""
        article = ARTICLE.search(ref)
        return bool(article) and any(article.group(0) in c for c in self.seen_citations)

    def result(self, rows: dict[int, Row], submitted: dict | None) -> dict:
        submitted = submitted or {'summary': '', 'file_issues': [], 'row_notes': [], 'questions': []}
        valid = set(rows)
        notes: dict[int, list[str]] = {}
        for n in submitted.get('row_notes', []):
            if n.get('row_id') in valid:
                notes.setdefault(n['row_id'], []).append(n.get('note', ''))
        issues = [{**i, 'row_ids': [r for r in i.get('row_ids', []) if r in valid],
                   'law_refs': [ref for ref in i.get('law_refs', []) if self.cited(ref)]}
                  for i in submitted.get('file_issues', [])]
        questions = [{**q, 'row_ids': [r for r in q.get('row_ids', []) if r in valid]} for q in submitted.get('questions', [])]
        for i in issues:
            # 일률 인하 묶음은 행이 모두 OK여도 협력사별 사유를 물어야 한다 (모델이 빠뜨려도 코드가 붙인다)
            asked = any(set(i['row_ids']) <= set(q['row_ids']) and '사유' in q['question'] for q in questions)
            if i.get('kind') == 'uniform_cut' and i['row_ids'] and not asked:
                questions.append({'row_ids': i['row_ids'], 'question': UNIFORM_CUT_QUESTION})
        out_rows = []
        for r in rows.values():
            # 수정안은 원래 위반 행이고, 다시 검사해서 위반이 사라진 것 중 마지막 것만 남긴다
            passed = [v for v in r.revisions if v['recheck_verdict'] != 'VIOLATION']
            out_rows.append({
                'row_id': r.row_id, 'verdict_code': r.verdict_code, 'codes': r.codes(), 'error': r.error,
                'messages': rule_messages(r),
                'notes': notes.get(r.row_id, []),
                'file_issues': [i for i in issues if r.row_id in i['row_ids']],
                'questions': [q['question'] for q in questions if r.row_id in q['row_ids']],
                'revision': passed[-1] if (passed and r.verdict_code == 'VIOLATION') else None,
            })
        return {'summary': guard_summary(submitted.get('summary', ''), issues), 'file_issues': issues, 'questions': questions,
                'rows': out_rows, 'trace': self.trace, 'usage': self.usage, 'model': self.model,
                'completed': submitted is not None and bool(submitted.get('summary') is not None)}
