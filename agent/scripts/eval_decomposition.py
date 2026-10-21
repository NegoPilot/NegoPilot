"""실제 모델로 쿼리 분해를 평가한다. shared/fixtures/decomposition 의 질문을 넣고 기대 결과와 비교한다.

API를 실제로 호출하므로 비용이 든다. 실행 전에 질문 수(--limit)와 모델·effort를 확인한다.

    uv run python scripts/eval_decomposition.py
    uv run python scripts/eval_decomposition.py --limit 3 --effort medium
    uv run python scripts/eval_decomposition.py --backend openai                        # gpt-4.1-mini (OPENAI_API_KEY)

결과는 eval_results/<시각>.json 에 저장한다. 프롬프트·모델·effort를 바꾸면 다시 돌려 전후를 비교한다.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from dataclasses import replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT.parent / "shared" / "src")]

from negopilot_shared import DecompositionResult  # noqa: E402

from negopilot_agent.decomposition import DecomposerSettings, QueryDecomposer  # noqa: E402

FIXTURES = sorted((ROOT.parent / "shared" / "fixtures" / "decomposition").glob("*.json"))


def load_dotenv(path: Path, skip: tuple[str, ...] = ()) -> None:
    """agent/.env 가 있으면 환경 변수로 읽는다. 이미 설정된 값과 skip에 있는 키는 건너뛴다."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if value.strip() and key.strip() not in skip:
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def score(expected: DecompositionResult, actual: DecompositionResult) -> dict:
    """항목별로 맞았는지(1/0). 하위 질문은 순서대로 짝지어 비교한다."""
    same_count = len(expected.sub_queries) == len(actual.sub_queries)
    pairs = list(zip(expected.sub_queries, actual.sub_queries))
    entities_ok = same_count and all(e.entities.model_dump(exclude_none=True)
                                     == a.entities.model_dump(exclude_none=True) for e, a in pairs)
    inputs_ok = same_count and all(
        [(i.slot, i.from_, i.select) for i in e.inputs] == [(i.slot, i.from_, i.select) for i in a.inputs]
        for e, a in pairs)
    return {
        "status": int(expected.status == actual.status),
        "count": int(same_count),
        "intents": int(Counter(s.intent for s in expected.sub_queries)
                       == Counter(s.intent for s in actual.sub_queries)),
        "entities": int(entities_ok),
        "dependencies": int(expected.parallel_groups() == actual.parallel_groups() and inputs_ok),
        "exact": int(expected.status == actual.status and same_count and entities_ok and inputs_ok
                     and [s.intent for s in expected.sub_queries] == [s.intent for s in actual.sub_queries]),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="앞에서부터 이 개수만 평가")
    parser.add_argument("--effort", help="low / medium / high ... (기본: 설정값, anthropic 전용)")
    parser.add_argument("--backend", choices=["anthropic", "openai"], help="기본: 설정값")
    parser.add_argument("--model", help="기본: 백엔드별 기본 모델")
    args = parser.parse_args()

    # --backend를 주면 .env의 모델명은 다른 백엔드용일 수 있어 읽지 않는다 (--model이나 셸 값은 그대로 쓴다)
    load_dotenv(ROOT / ".env", skip=("NEGOPILOT_DECOMPOSER_MODEL",) if args.backend else ())
    if args.backend:
        os.environ["NEGOPILOT_DECOMPOSER_BACKEND"] = args.backend
    settings = DecomposerSettings()
    if args.model:
        settings = replace(settings, model=args.model)
    if args.effort:
        settings = replace(settings, effort=args.effort)
    decomposer = QueryDecomposer(settings=settings)
    cases = FIXTURES[: args.limit] if args.limit else FIXTURES
    effort = f" · effort {settings.effort}" if settings.backend == "anthropic" else ""
    print(f"백엔드 {settings.backend} · 모델 {settings.model}{effort} · 질문 {len(cases)}개")

    rows = []
    for path in cases:
        data = json.loads(path.read_text(encoding="utf-8"))
        expected = DecompositionResult.model_validate(data["expected"])
        started = time.perf_counter()
        try:
            actual = await decomposer.decompose(data["query"])
        except Exception as err:  # 한 질문의 오류(시간 초과 등)로 평가 전체가 멈추지 않게 오답으로 기록한다
            elapsed = time.perf_counter() - started
            rows.append({"case": path.stem, "query": data["query"], "seconds": round(elapsed, 2),
                         **{k: 0 for k in ("status", "count", "intents", "entities", "dependencies", "exact")},
                         "error": f"{type(err).__name__}: {err}"[:300], "actual": None})
            print(f"! {path.stem:<38} {elapsed:5.1f}s  오류 {type(err).__name__}", flush=True)
            continue
        elapsed = time.perf_counter() - started
        s = score(expected, actual)
        rows.append({"case": path.stem, "query": data["query"], "seconds": round(elapsed, 2), **s,
                     "actual": actual.model_dump(mode="json", by_alias=True)})
        mark = "✓" if s["exact"] else "✗"
        print(f"{mark} {path.stem:<38} {elapsed:5.1f}s  " + " ".join(f"{k}={v}" for k, v in s.items() if k != "exact"),
              flush=True)

    keys = ["status", "count", "intents", "entities", "dependencies", "exact"]
    summary = {k: round(sum(r[k] for r in rows) / len(rows), 3) for k in keys}
    print("\n정확도  " + "  ".join(f"{k} {v:.0%}" for k, v in summary.items()))

    out_dir = ROOT / "eval_results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"backend": settings.backend, "model": settings.model, "effort": settings.effort, "summary": summary,
                               "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
