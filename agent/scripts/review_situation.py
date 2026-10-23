"""상황별 법률 검토 CLI. 기존 /review 화면과 별도이며 모델 API 비용이 발생한다."""
import argparse
import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from ask import load_env


async def main(args):
    from negopilot_agent import timing
    from negopilot_agent.legal_review.situation import SituationReviewer
    with timing.collect() as measured:
        result = await SituationReviewer().review(args.query, args.as_of)
    if args.json:
        print(json.dumps(result.model_dump(mode='json') | {'timings': measured.summary()},
                         ensure_ascii=False, indent=2))
    else:
        print(result.answer.text)
        for notice in result.answer.notices:
            print(f'안내: {notice}')
        for citation in result.answer.citations:
            print(f'[{citation.n}] {citation.label}')
        print(measured.report())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('query')
    parser.add_argument('--as-of', default=datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat())
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    load_env()
    asyncio.run(main(args))
