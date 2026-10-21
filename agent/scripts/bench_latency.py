"""Agent 전체(분해 → Routing → 생성)를 실제 모델과 MCP 서버로 돌려 단계별 시간을 잰다.

어디서 시간이 드는지 보고, 속도 개선 전후를 같은 질문으로 비교할 때 쓴다. 정답은 채점하지 않는다
(분해 정확도는 eval_decomposition.py, 답변 품질은 eval_generation.py).

먼저 MCP 서버를 띄운다 (README 빠른 시작):
    cd mcp-servers/business-mcp && uv run python server.py --http 9102
    cd mcp-servers/law-mcp      && uv run python server.py --http 9103

    uv run python scripts/bench_latency.py                          # 개발용 질문 11개 × 1회
    uv run python scripts/bench_latency.py --set validation --repeat 2
    uv run python scripts/bench_latency.py --limit 3 --verbose      # 질문마다 시간 표 출력

질문은 하나씩 순서대로 돌린다 (동시에 돌리면 서로의 대기 시간이 섞인다).
모델 호출 비용이 든다. 결과는 eval_results/latency-<시각>.json 에 저장한다.
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT.parent / "shared" / "src"), str(ROOT / "scripts")]

from ask import MODEL_KEYS, load_env  # noqa: E402

SETS = {"dev": "decomposition", "validation": "validation"}
STAGES = ["total", "decompose_query", "route", "generate", "llm_seconds", "tool_seconds"]
COUNTS = ["llm_calls", "tool_calls", "input_tokens", "output_tokens", "cached_tokens"]


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p * (len(ordered) - 1)))]


def aggregate(rows: list[dict]) -> dict:
    ok = [r["timings"] for r in rows if "timings" in r]
    out = {}
    for key in STAGES:
        values = [t.get(key, 0.0) for t in ok]
        if values:
            out[key] = {"mean": round(statistics.mean(values), 2), "p50": round(percentile(values, 0.5), 2),
                        "p90": round(percentile(values, 0.9), 2), "max": round(max(values), 2)}
    for key in COUNTS:
        values = [t.get(key, 0) for t in ok]
        if values:
            out[key] = {"mean": round(statistics.mean(values), 1), "max": max(values)}
    costs = [t["cost_usd"] for t in ok if "cost_usd" in t]
    if costs:
        out["cost_usd"] = {"mean": round(statistics.mean(costs), 6), "max": round(max(costs), 6),
                           "total": round(sum(costs), 6), "unknown_calls": sum(t.get("cost_unknown_calls", 0) for t in ok)}
    return out


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", dest="fixture_set", choices=list(SETS), default="dev", help="기본: dev")
    parser.add_argument("--limit", type=int, help="앞에서부터 이 개수만")
    parser.add_argument("--repeat", type=int, default=1, help="질문마다 N회")
    parser.add_argument("--backend", choices=["anthropic", "openai"], help="분해·생성 백엔드를 한 번에 지정")
    parser.add_argument("--verbose", action="store_true", help="질문마다 시간 표 출력")
    args = parser.parse_args()

    load_env(skip=MODEL_KEYS if args.backend else ())
    if args.backend:
        os.environ["NEGOPILOT_DECOMPOSER_BACKEND"] = os.environ["NEGOPILOT_GENERATOR_BACKEND"] = args.backend

    from negopilot_agent import timing   # 환경 변수를 읽은 뒤 import (설정 기본값이 env를 본다)
    from negopilot_agent.decomposition import DecomposerSettings
    from negopilot_agent.generation import GeneratorSettings
    from negopilot_agent.graph import build_graph

    cases = sorted((ROOT.parent / "shared" / "fixtures" / SETS[args.fixture_set]).glob("*.json"))
    cases = cases[: args.limit] if args.limit else cases
    d, g = DecomposerSettings(), GeneratorSettings()
    print(f"분해 {d.backend}/{d.model} · 생성 {g.backend}/{g.model} · {args.fixture_set} 질문 {len(cases)}개"
          f" × {args.repeat}회")
    print(f"{'질문':<36} {'전체':>6} {'분해':>6} {'Routing':>7} {'생성':>6}  LLM  도구  출력토큰")

    graph = build_graph()
    rows = []
    for run in range(1, args.repeat + 1):
        for path in cases:
            query = json.loads(path.read_text(encoding="utf-8"))["query"]
            row = {"case": path.stem, "run": run}
            try:
                with timing.collect() as t:
                    state = await graph.ainvoke({"query": query})
            except Exception as err:  # 한 질문의 오류로 측정 전체가 멈추지 않게 기록만 한다
                row["error"] = f"{type(err).__name__}: {err}"[:300]
                print(f"! {path.stem:<34} 오류 {type(err).__name__}", flush=True)
                rows.append(row)
                continue
            s = t.summary()
            row |= {"timings": s, "kind": state["answer"].kind,
                    "spans": [{"kind": x.kind, "name": x.name, "stage": x.stage, "start": round(x.start, 3),
                               "seconds": round(x.seconds, 3), **x.meta} for x in t.spans]}
            rows.append(row)
            print(f"  {path.stem:<34} {s['total']:5.1f}s {s.get('decompose_query', 0):5.1f}s "
                  f"{s.get('route', 0):6.1f}s {s.get('generate', 0):5.1f}s  {s['llm_calls']:>3}  {s['tool_calls']:>4}"
                  f"  {s['output_tokens']:>6}", flush=True)
            if args.verbose:
                print(t.report() + "\n")

    summary = aggregate(rows)
    print("\n단계        평균    p50    p90    최대")
    for key in STAGES:
        if v := summary.get(key):
            print(f"{key:<16} {v['mean']:5.2f}s {v['p50']:5.2f}s {v['p90']:5.2f}s {v['max']:5.2f}s")
    for key in COUNTS:
        if v := summary.get(key):
            print(f"{key:<16} 평균 {v['mean']}  최대 {v['max']}")
    if v := summary.get("cost_usd"):
        unknown = f"  (단가 모르는 호출 {v['unknown_calls']}개 제외)" if v["unknown_calls"] else ""
        print(f"{'cost_usd':<16} 질문당 평균 ${v['mean']:.5f}  최대 ${v['max']:.5f}  합계 ${v['total']:.4f}"
              f"  (1,000개 질문이면 약 ${v['mean'] * 1000:.2f}){unknown}")

    out_dir = ROOT / "eval_results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"latency-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"decomposer": {"backend": d.backend, "model": d.model},
                               "generator": {"backend": g.backend, "model": g.model},
                               "set": args.fixture_set, "repeat": args.repeat, "summary": summary, "rows": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
