"""정답 세트(eval/retrieval_gold.json)의 원문 질문을 실제 분해기(agent decomposer)로 분해해 저장한다.

eval_retrieval.py --decomposed 로 넘기면, 라우터처럼 law_check 하위 질문(question + law 엔티티)으로 검색해
"원문 → 사람 분해 → 실제 분해" 검색 점수를 비교한다. 분해 프롬프트나 모델을 바꾸면 이 스크립트를 다시 돌린다.

agent 가상환경에서 실행한다 (분해 API를 호출해 비용이 든다. gpt-4.1-mini 로 질문 74개 × 3회 기준 몇 센트):
    cd agent && uv run python ../mcp-servers/law-mcp/eval/decompose_gold.py
    cd agent && uv run python ../mcp-servers/law-mcp/eval/decompose_gold.py --backend anthropic --model claude-haiku-5-5

결과는 eval/results/decomposed_<모델>.json 에 저장한다.
"""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
AGENT = ROOT / "agent"
sys.path[:0] = [str(AGENT / "src"), str(ROOT / "shared" / "src"), str(AGENT / "scripts")]

from eval_decomposition import load_dotenv  # noqa: E402

from negopilot_agent.decomposition import DecomposerSettings, QueryDecomposer  # noqa: E402


async def decompose_all(decomposer: QueryDecomposer, items: list[dict], concurrency: int) -> dict:
    sem = asyncio.Semaphore(concurrency)

    async def one(it: dict) -> tuple[str, dict]:
        async with sem:
            try:
                r = await decomposer.decompose(it["query"])
            except Exception as err:  # 한 질문의 오류로 전체가 멈추지 않게 실패로 기록한다
                print(f"! {it['id']:<28} 오류 {type(err).__name__}", flush=True)
                return it["id"], {"status": "error", "error": f"{type(err).__name__}: {err}"[:300],
                                  "sub_queries": []}
        subs = [{"id": s.id, "intent": s.intent, "question": s.question, "original_text": s.original_text,
                 "entities": s.entities.model_dump(exclude_none=True)} for s in r.sub_queries]
        laws = [s["question"] for s in subs if s["intent"] == "law_check"]
        print(f"{'✓' if laws else '-'} {it['id']:<28} {r.status:<7} {' | '.join(laws) or [s['intent'] for s in subs]}",
              flush=True)
        return it["id"], {"status": r.status, "sub_queries": subs}

    return dict(await asyncio.gather(*(one(it) for it in items)))


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--backend", choices=["anthropic", "openai"], help="기본: agent 설정값 (openai)")
    p.add_argument("--model", help="기본: 백엔드별 기본 모델")
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--repeat", type=int, default=3,
                   help="같은 질문을 N번 분해한다. 실행마다 분해가 조금씩 달라 평균으로 비교한다")
    args = p.parse_args()

    load_dotenv(AGENT / ".env", skip=("NEGOPILOT_DECOMPOSER_MODEL",) if args.backend else ())
    if args.backend:
        os.environ["NEGOPILOT_DECOMPOSER_BACKEND"] = args.backend
    settings = DecomposerSettings()
    if args.model:
        settings = replace(settings, model=args.model)
    decomposer = QueryDecomposer(settings=settings)

    gold = json.loads((HERE / "retrieval_gold.json").read_text(encoding="utf-8"))
    print(f"백엔드 {settings.backend} · 모델 {settings.model} · 질문 {len(gold['items'])}개 × {args.repeat}회")
    runs = []
    for i in range(args.repeat):
        print(f"\n[{i + 1}/{args.repeat}회]")
        results = await decompose_all(decomposer, gold["items"], args.concurrency)
        runs.append({it["id"]: results[it["id"]] for it in gold["items"]})

    out = HERE / "results" / f"decomposed_{settings.model}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"backend": settings.backend, "model": settings.model, "runs": runs},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
