"""회사(협력사·RFQ)와 조건으로 협상안을 만들고, 협력사 제안서(PDF·DOCX)와 내부 브리핑(PDF)을 저장한다 (#231, #236).

business-mcp를 먼저 띄운다:
    cd mcp-servers/business-mcp && uv run python server.py --http 9102

    uv run python scripts/propose_offer.py RFQ-2026-002 SUP-0060 --payment-days 90 --out-dir out/
    uv run python scripts/propose_offer.py RFQ-2026-002 SUP-0060 --json
    uv run python scripts/propose_offer.py --excel offers.xlsx --out-dir out/      # 한 행 = 요청 하나, summary.csv도 저장
    uv run python scripts/propose_offer.py --template offer_template.xlsx         # 엑셀 양식 만들기
조건을 비우면 RFQ 목표 단가, 시장 견적, 기존 계약 값으로 채운다.
모델 협상 문구는 기본으로 켜져 있다(OPENAI_API_KEY, 비용 발생). --no-writing이면 코드 문구만 쓴다.
"""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ask import load_env

CONDITIONS = {'target_price': int, 'max_price': int, 'payment_days': int, 'raw_material_ratio': float,
              'energy_ratio': float, 'contract_days': int, 'notes': str}


def single_request(args):
    from negopilot_agent.offer_proposal import OfferProposalRequest
    body = {'rfq_id': args.rfq_id, 'supplier': args.supplier, 'as_of': args.as_of,
            **{k: getattr(args, k) for k in CONDITIONS if getattr(args, k) is not None}}
    if args.intent:
        body['intent'] = args.intent
    if args.price_linkage is not None:
        body['price_linkage'] = args.price_linkage == 'yes'
    if args.linkage_waived:
        body['linkage_waived_by_agreement'] = True
    return OfferProposalRequest.model_validate(body, strict=False)


async def main(args):
    from negopilot_agent import timing
    from negopilot_agent.api.offer_api import summary_line
    from negopilot_agent.offer_proposal import excel, render
    from negopilot_agent.offer_proposal.service import build_package, package_files
    from negopilot_agent.offer_proposal.writer import NegotiationWriter
    from negopilot_agent.routing.mcp_client import McpToolCaller

    if args.template:
        Path(args.template).write_bytes(excel.template_bytes())
        print(f'양식 저장: {args.template}')
        return
    caller = McpToolCaller()
    writer = None if args.no_writing else NegotiationWriter()
    out_dir = Path(args.out_dir) if args.out_dir else None

    if args.excel:
        path = Path(args.excel)
        rows = excel.parse_rows(path.read_bytes(), path.name, args.as_of)

        async def produce(req):
            async with caller.session():
                pkg = await build_package(req, caller, writer)
            return summary_line(pkg), package_files(pkg)

        with timing.collect() as measured:
            summary, files = await excel.run_batch(rows, produce)
        for line in summary:
            print(f"{line['row']:>3}행 {line.get('supplier', ''):9} {line.get('status', ''):8} "
                  f"{line.get('unit_price', '')!s:>6} {line.get('error', '') or ''}")
        if out_dir:
            excel.write_out_dir(out_dir, files, summary)
            print(f'{len(files)}개 파일과 summary.csv 저장: {out_dir}')
        print(measured.report().splitlines()[0])
        return

    with timing.collect() as measured:
        async with caller.session():
            pkg = await build_package(single_request(args), caller, writer)
    if args.json:
        print(json.dumps({'offer_document': pkg.document.model_dump(mode='json') if pkg.document else None,
                          'check': pkg.check(),
                          'internal_briefing': pkg.briefing(), 'timings': measured.summary()},
                         ensure_ascii=False, indent=2))
    else:
        print(render(pkg.proposal))
        if pkg.writing:
            print(f'\n[협상 문구: {pkg.writing.source}, 시도 {pkg.writing.attempts}회]\n{pkg.writing.strategy}')
            print(f'제안 멘트(초안): {pkg.writing.opening_message}')
        print(measured.report())
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, data in package_files(pkg).items():
            (out_dir / name).write_bytes(data)
            print(f'저장: {out_dir / name}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('rfq_id', nargs='?')
    parser.add_argument('supplier', nargs='?', help='SUP-0001 형식')
    parser.add_argument('--as-of', default=datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat(),
                        help='기준일. 엑셀에서는 기준일 칸이 빈 행에 쓴다')
    parser.add_argument('--intent', choices=['target', 'market', 'payment'], help='먼저 고를 기준')
    for name, kind in CONDITIONS.items():
        parser.add_argument('--' + name.replace('_', '-'), type=kind)
    parser.add_argument('--price-linkage', choices=['yes', 'no'])
    parser.add_argument('--linkage-waived', action='store_true', help='연동하지 않기로 합의함')
    parser.add_argument('--excel', help='.xlsx 또는 .csv. 한 행 = 요청 하나')
    parser.add_argument('--out-dir', help='제안서 PDF·DOCX, 내부 브리핑 PDF(, summary.csv)를 저장할 폴더')
    parser.add_argument('--template', help='엑셀 양식을 이 경로에 만들고 끝낸다')
    parser.add_argument('--no-writing', action='store_true', help='모델 협상 문구를 쓰지 않는다 (비용 없음)')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    if not (args.template or args.excel or (args.rfq_id and args.supplier)):
        parser.error('rfq_id와 supplier, 또는 --excel, --template 중 하나가 필요하다')
    load_env()
    asyncio.run(main(args))
