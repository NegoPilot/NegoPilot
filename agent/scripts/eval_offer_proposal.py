"""협상안 생성(#231)을 실제 business-mcp로 평가한다. 모델을 부르지 않으므로 비용이 없다.

사례: eval_data/offer_proposal_cases.json (회사 + 조건, OfferProposalRequest와 같은 필드). business-mcp를 먼저 띄운다:
    cd mcp-servers/business-mcp && uv run python server.py --http 9102

    uv run python scripts/eval_offer_proposal.py
    uv run python scripts/eval_offer_proposal.py --pdf-dir /tmp/offers     # 사례별 PDF도 저장

사례마다 확인하는 것:
- 결과 형태: 협상안 정확히 하나, 또는 이유가 있는 no_offer
- 위반 0건: 제시가·목표 합의가·최대 수용가 조건으로 check_offer를 따로 다시 불러 violation이 없는지
- 근거 일치: rationale의 값이 요청 조건이나 get_price_benchmarks를 따로 불러 얻은 값과 같은지
- 단가 규칙: 고른 기준(목표가 / 시장가 / 지급 양보)과 최대 수용가로 다시 계산한 단가와 같은지
- 협상 범위: 제시가 ≤ 목표 합의가 ≤ 최대 수용가, 요청 최대 수용가를 넘지 않음
- 조정: 법 기준을 넘는 요청 조건(지급 60일 초과, 연동 대상인데 연동 없음)이 고쳐지고 adjustments에 적혔는지
- 고친 내역: PAYMENT_DAYS_OVER_60이면 60일로, 연동 누락이면 연동 포함으로 고쳤고 최종 조건에 반영됐는지
- PDF: 오류 없이 만들어지고, PDF 내용(sections)의 숫자가 JSON 값과 같은지
결과는 eval_results/offer_proposal_<시각>.json 에 저장한다.
"""

import argparse
import asyncio
import json
import math
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path

from ask import load_env

ROOT = Path(__file__).resolve().parents[1]
CASE_META = {'id', 'note'}


def lookup(bench: dict, offer: dict, req: dict, source: str):
    section, key = source.split('.', 1)
    if section == 'offer':
        return offer[key]
    if section == 'request':
        return req[key]
    return bench[section][key]


def ceil_won(value: float) -> int:
    return math.ceil(round(value, 6))


def expected_price(bench: dict, req: dict, basis: str) -> int:
    rules = bench['rules']
    target = req.get('target_price') or bench['rfq']['target_price']
    median, last = bench['market']['median'], bench['supplier']['last_price']
    if basis == 'market':
        price = round(median)
    elif basis == 'payment':
        price = target
    else:
        floors = [target]
        if median:
            floors.append(ceil_won(median * rules['MARKET_PRICE_FLOOR']))
        if last:
            floors.append(ceil_won(last * (1 - rules['LAST_QUOTE_CUT'])))
        price = max(floors)
    return min(price, req['max_price']) if req.get('max_price') else price


def expected_adjustments(bench: dict, req: dict, offer: dict) -> bool:
    """요청 조건이 법 기준을 넘으면 고치고 적었는지."""
    limit = bench['rules']['PAYMENT_DAYS_LIMIT']
    ok = True
    if req.get('payment_days', 0) > limit:
        ok &= offer['payment_days'] == limit and any(f'지급 {req["payment_days"]}일' in a for a in offer['adjustments'])
    if req.get('price_linkage') is False and offer['price_linkage']:
        ok &= any('연동 포함' in a for a in offer['adjustments'])
    return ok


def revisions_ok(offer: dict, rules: dict) -> bool:
    for r in offer['revisions']:
        if r['code'] == 'PAYMENT_DAYS_OVER_60':
            if not (r['before'] > rules['PAYMENT_DAYS_LIMIT'] == r['after'] == offer['payment_days']):
                return False
        elif r['field'] == 'price_linkage':
            if not (r['before'] is False and r['after'] is True and offer['price_linkage'] is True):
                return False
        else:
            return False
    return True


def pdf_ok(proposal, out: dict) -> tuple[bool, bytes]:
    """PDF가 만들어지고, PDF에 들어간 숫자(sections)가 JSON 값과 같은지."""
    from pypdf import PdfReader
    from negopilot_agent.offer_proposal.pdf import render_pdf, sections
    data = render_pdf(proposal)
    pages = len(PdfReader(BytesIO(data)).pages)
    doc = dict(sections(proposal))
    offer = out['offer']
    if offer is None:
        return pages >= 1 and doc['이유와 바꿔야 할 조건'][0][1] == out['no_offer_reasons'], data
    table = dict(doc['제시 조건'][0][1])
    price_rows = doc['협상 범위'][0][1][1:]
    same = (table['단가'] == f"{offer['unit_price']:,}원"
            and table['예상 계약금액'] == f"{offer['contract_amount']:,}원"
            and table['계약 기간'] == f"{offer['contract_days']}일"
            and f"{offer['payment_days']}일" in table['지급 조건']
            and [r[1] for r in price_rows] == [f"{p['unit_price']:,}원" for p in offer['price_range']]
            and doc['양보 순서'][0][1] == [f'{n}. {t}' for n, t in enumerate(offer['concessions'], 1)]
            and doc['결론'][0][1] == offer['headline'])
    return pages >= 1 and same, data


async def evaluate(case: dict, as_of: str, caller, pdf_dir: Path | None) -> dict:
    from negopilot_agent.offer_proposal import OfferProposalRequest, OfferProposer
    req = {k: v for k, v in case.items() if k not in CASE_META}
    request = OfferProposalRequest.model_validate({'as_of': as_of, **req}, strict=False)
    started = time.perf_counter()
    proposal = await OfferProposer(caller).propose(request)
    seconds = time.perf_counter() - started
    out = proposal.model_dump(mode='json')
    rendered, pdf = pdf_ok(proposal, out)
    if pdf_dir:
        (pdf_dir / f"{case['id']}.pdf").write_bytes(pdf)
    row = {'case': case['id'], 'status': out['status'], 'seconds': round(seconds, 3), 'pdf_ok': rendered}
    bench = await caller('business-mcp', 'get_price_benchmarks', {'rfq_id': case['rfq_id'], 'supplier': case['supplier']})
    offer = out['offer']
    if out['status'] == 'no_offer':
        row |= {'shape_ok': offer is None and bool(out['no_offer_reasons']), 'violations': 0, 'basis_total': 0,
                'basis_match': 0, 'price_ok': None, 'range_ok': None, 'adjust_ok': None, 'revisions_ok': None,
                'reasons': out['no_offer_reasons']}
        return row
    violations = 0
    for point in offer['price_range']:
        recheck = await caller('business-mcp', 'check_offer', {
            'supplier': out['supplier_id'], 'rfq_id': case['rfq_id'], 'unit_price': point['unit_price'],
            'payment_days': offer['payment_days'], 'price_linkage': offer['price_linkage'],
            'contract_days': offer['contract_days'], 'as_of': as_of,
            'linkage_waived_by_agreement': offer['linkage_waived_by_agreement'],
            **({'raw_material_ratio': offer['raw_material_ratio']} if offer['raw_material_ratio'] is not None else {}),
            **({'energy_ratio': offer['energy_ratio']} if offer['energy_ratio'] is not None else {})})
        violations += len(recheck['violations'])
    matches = [lookup(bench, offer, req, b['source']) == b['value'] for b in offer['rationale']]
    prices = [p['unit_price'] for p in offer['price_range']]
    row |= {
        'shape_ok': offer is not None and not out['no_offer_reasons'],
        'basis': offer['basis'], 'unit_price': offer['unit_price'], 'payment_days': offer['payment_days'],
        'price_linkage': offer['price_linkage'], 'contract_days': offer['contract_days'], 'prices': prices,
        'target_price': out['target_price'], 'last_price': bench['supplier']['last_price'],
        'market_median': bench['market']['median'], 'existing_payment_days': bench['supplier']['payment_days'],
        'verdict_code': offer['verdict_code'],
        'violations': violations + (offer['verdict_code'] == 'VIOLATION'),
        'warnings': [w['code'] for w in offer['warnings']],
        'basis_total': len(matches), 'basis_match': sum(matches),
        'price_ok': offer['unit_price'] == expected_price(bench, req, offer['basis']),
        'range_ok': prices == sorted(prices) and (not req.get('max_price') or prices[-1] <= max(req['max_price'], prices[0])),
        'adjust_ok': expected_adjustments(bench, req, offer), 'adjustments': offer['adjustments'],
        'headline_ok': f"{offer['unit_price']:,}원" in offer['headline'] and f"{offer['payment_days']}일" in offer['headline'],
        'revisions': offer['revisions'], 'revisions_ok': revisions_ok(offer, bench['rules']),
        'concessions': len(offer['concessions']), 'objections': len(offer['objections']),
        'filled': [f['field'] for f in out['filled']],
    }
    return row


def passed(row: dict) -> bool:
    return (row['shape_ok'] and row['pdf_ok'] and row['violations'] == 0 and row['basis_match'] == row['basis_total']
            and all(row.get(k) is not False for k in ('price_ok', 'range_ok', 'adjust_ok', 'revisions_ok')))


async def main(args):
    from negopilot_agent.routing.mcp_client import McpToolCaller
    data = json.loads((ROOT / 'eval_data' / 'offer_proposal_cases.json').read_text(encoding='utf-8'))
    cases = [c for c in data['cases'] if not args.case or c['id'] in args.case]
    pdf_dir = Path(args.pdf_dir) if args.pdf_dir else None
    if pdf_dir:
        pdf_dir.mkdir(parents=True, exist_ok=True)
    caller = McpToolCaller()
    rows = []
    for case in cases:
        async with caller.session():
            row = await evaluate(case, data['as_of'], caller, pdf_dir)
        rows.append(row)
        detail = (f"{row['basis']:7} 제시 {row['prices'][0]:>6,} 합의 {row['prices'][1]:>6,} 수용 {row['prices'][2]:>6,} "
                  f"지급 {row['payment_days']:>2}일 연동 {'O' if row['price_linkage'] else 'X'} "
                  f"경고 {len(row['warnings'])} 조정 {len(row['adjustments'])} 고침 {len(row['revisions'])}"
                  if row['status'] == 'offer' else 'no_offer: ' + '; '.join(row['reasons']))
        print(f"{'✓' if passed(row) else '✗'} {row['case']:36} {row['seconds']:.2f}s  {detail}")
    offers = [r for r in rows if r['status'] == 'offer']
    seconds = sorted(r['seconds'] for r in rows)
    count = lambda key: f"{sum(bool(r[key]) for r in offers)}/{len(offers)}"   # noqa: E731
    summary = {
        'cases': len(rows), 'passed': sum(passed(r) for r in rows), 'offers': len(offers),
        'no_offers': len(rows) - len(offers), 'shape_ok': sum(r['shape_ok'] for r in rows),
        'pdf_ok': sum(r['pdf_ok'] for r in rows),
        'presented_violations': sum(r['violations'] for r in offers),
        'basis_match': f"{sum(r['basis_match'] for r in offers)}/{sum(r['basis_total'] for r in offers)}",
        'price_rule_ok': count('price_ok'), 'range_ok': count('range_ok'), 'adjust_ok': count('adjust_ok'),
        'headline_ok': count('headline_ok'), 'revisions_ok': count('revisions_ok'),
        'adjusted_offers': sum(bool(r['adjustments']) for r in offers),
        'revised_offers': sum(bool(r['revisions']) for r in offers),
        'seconds_mean': round(sum(seconds) / len(seconds), 3), 'seconds_max': seconds[-1],
        'basis_counts': {b: sum(r.get('basis') == b for r in offers) for b in ('target', 'market', 'payment')},
    }
    print('\n' + '  '.join(f'{k} {v}' for k, v in summary.items() if k != 'basis_counts'))
    print('기준별 선택', summary['basis_counts'])
    out_dir = ROOT / 'eval_results'
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"offer_proposal_{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({'as_of': data['as_of'], 'summary': summary, 'rows': rows}, ensure_ascii=False, indent=2),
                    encoding='utf-8')
    print(f'저장: {path.relative_to(ROOT)}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--case', action='append', help='이 id만 실행 (여러 번 지정 가능)')
    parser.add_argument('--pdf-dir', help='사례별 PDF를 저장할 폴더')
    args = parser.parse_args()
    load_env()
    asyncio.run(main(args))
