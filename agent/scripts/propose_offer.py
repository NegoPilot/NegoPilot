"""회사(협력사·RFQ)와 조건으로 협상안 하나를 만들어 출력하거나 PDF로 저장한다 (#231).

모델을 부르지 않으므로 비용이 없다. business-mcp를 먼저 띄운다:
    cd mcp-servers/business-mcp && uv run python server.py --http 9102

    uv run python scripts/propose_offer.py RFQ-2026-002 SUP-0060
    uv run python scripts/propose_offer.py RFQ-2026-002 SUP-0060 --payment-days 90 --max-price 2000 --pdf out.pdf
    uv run python scripts/propose_offer.py RFQ-2026-002 SUP-0060 --json
조건을 비우면 RFQ 목표 단가, 시장 견적, 기존 계약 값으로 채우고 채운 값을 출력한다.
"""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ask import load_env

CONDITIONS = {'intent': str, 'target_price': int, 'max_price': int, 'payment_days': int, 'raw_material_ratio': float,
              'energy_ratio': float, 'contract_days': int, 'notes': str}


async def main(args):
    from negopilot_agent import timing
    from negopilot_agent.offer_proposal import OfferProposalRequest, OfferProposer, render
    from negopilot_agent.offer_proposal.pdf import render_pdf
    body = {'rfq_id': args.rfq_id, 'supplier': args.supplier, 'as_of': args.as_of,
            **{k: getattr(args, k) for k in CONDITIONS if getattr(args, k) is not None}}
    if args.price_linkage is not None:
        body['price_linkage'] = args.price_linkage == 'yes'
    if args.linkage_waived:
        body['linkage_waived_by_agreement'] = True
    request = OfferProposalRequest.model_validate(body, strict=False)
    with timing.collect() as measured:
        proposal = await OfferProposer().propose(request)
    if args.json:
        print(json.dumps(proposal.model_dump(mode='json') | {'timings': measured.summary()},
                         ensure_ascii=False, indent=2))
    else:
        print(render(proposal))
        print(measured.report())
    if args.pdf:
        Path(args.pdf).write_bytes(render_pdf(proposal))
        print(f'PDF 저장: {args.pdf}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('rfq_id')
    parser.add_argument('supplier', help='SUP-0001 형식')
    parser.add_argument('--as-of', default=datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat())
    parser.add_argument('--intent', choices=['target', 'market', 'payment'], help='먼저 고를 기준')
    for name, kind in CONDITIONS.items():
        if name != 'intent':
            parser.add_argument('--' + name.replace('_', '-'), type=kind)
    parser.add_argument('--price-linkage', choices=['yes', 'no'])
    parser.add_argument('--linkage-waived', action='store_true', help='연동하지 않기로 합의함')
    parser.add_argument('--pdf', help='PDF로 저장할 경로')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    load_env()
    asyncio.run(main(args))
