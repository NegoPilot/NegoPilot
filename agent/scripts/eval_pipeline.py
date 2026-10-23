"""실제 그래프의 최종 품질·토큰·시간 평가. MCP 서버와 모델 키가 필요하다.

uv run python scripts/eval_pipeline.py --set dev --limit 3 --repeat 3
uv run python scripts/eval_pipeline.py --set validation --no-judge
uv run python scripts/eval_pipeline.py --set validation --cross-judge gpt-5.4-mini   # 같은 답변을 다른 모델로도 채점

Generation 평가의 질문과 정답을 재사용하되 저장된 분해·Routing을 실행 입력으로
쓰지 않는다. 채점은 별도 호출이며 실제 Agent의 토큰/시간과 분리한다.
"""

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT.parent / "shared" / "src"), str(ROOT / "scripts")]

from ask import load_env  # noqa: E402
from bench_latency import aggregate  # noqa: E402
from eval_generation import SETS, judge, load_case, offline_metrics  # noqa: E402

# 개념 설명·범위 밖은 근거 없이 답하는 게 맞으므로 근거 표시율에서 뺀다
NO_GROUNDING = {"general_knowledge", "out_of_scope"}
from negopilot_agent import timing  # noqa: E402
from negopilot_shared import RouteResult  # noqa: E402


async def evaluate_case(graph, item, input_dir, judge_client=None, judge_model="gpt-4.1-mini", cross_model=None):
    query, expected_decomposition, _ = load_case(item["fixture"], input_dir)
    row = {"case": item["fixture"], "query": query, "points": len(item["points"])}
    with timing.collect() as measured:
        try:
            state = await graph.ainvoke({"query": query})
            answer = state["answer"]
            routing = state.get("routing") or RouteResult(status="not_routed")
            intents = [sq.intent for sq in state["decomposition"].sub_queries]
            expected = [sq.intent for sq in expected_decomposition.sub_queries]
            # 분해 intent가 정답 분해와 같은지, 근거가 필요한 답변에 근거 표시가 붙었는지 (#110)
            row.update(intents=intents, expected_intents=expected, intent_match=intents == expected,
                       grounded=answer.grounded,
                       needs_grounding=answer.kind == "answer" and not set(intents) <= NO_GROUNDING)
            row.update(kind=answer.kind, kind_ok=answer.kind == item["kind"],
                       answer=answer.model_dump(mode="json"),
                       decomposition=state["decomposition"].model_dump(mode="json", by_alias=True),
                       routing=routing.model_dump(mode="json"))
            row.update(offline_metrics(item, state["decomposition"], routing))
            row["tool_errors"] = sum(
                call.error is not None
                for outcome in routing.outcomes
                for call in [*outcome.calls, *outcome.referenced_articles])
            verification = answer.verification
            row.update(retried=bool(verification and verification.attempts > 1),
                       masked=bool(verification and verification.masked))
        except Exception as error:
            row.update(error=f"{type(error).__name__}: {error}"[:300], kind_ok=False)
    # 실패·재시도에 사용한 토큰도 분모에서 빠지지 않게 보존한다.
    row["timings"] = measured.summary()
    row["spans"] = [vars(span) for span in measured.spans]
    if "error" in row:
        return row
    if not item["points"]:
        row["judge_status"] = "not_required"
    elif answer.kind != "answer":
        row.update(judge_status="wrong_kind", points_covered=0)
    elif judge_client is None:
        row["judge_status"] = "disabled"
    else:
        # 채점 호출은 Agent 측정 범위 밖에서 별도로 기록한다.
        with timing.collect() as judge_timing:
            try:
                row.update(await judge(judge_client, judge_model, item, answer))
                row["judge_status"] = "ok"
                if cross_model:   # 같은 답변을 다른 모델로도 채점 (생성 모델과 같은 채점 모델은 후할 수 있다)
                    row["cross"] = await judge(judge_client, cross_model, item, answer)
            except Exception as error:
                row.update(judge_status="error", judge_error=f"{type(error).__name__}: {error}"[:300])
        row["judge_timings"] = judge_timing.summary()
    return row


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def cross_metrics(rows):
    """교차 채점 비교. agreement = (답변, 정답 포인트) 짝 중 두 채점이 같게 판정한 비율."""
    crossed = [row for row in rows if "cross" in row and "covered_points" in row]
    if not crossed:
        return None
    points = sum(row["points"] for row in crossed)
    agree = sum(row["points"] - len(set(row["covered_points"]) ^ set(row["cross"]["covered_points"])) for row in crossed)
    return {"cases": len(crossed),
            "point_coverage": ratio(sum(row["points_covered"] for row in crossed), points),
            "cross_point_coverage": ratio(sum(row["cross"]["points_covered"] for row in crossed), points),
            "unsupported_claims": sum(len(row.get("unsupported_claims", [])) for row in crossed),
            "cross_unsupported_claims": sum(len(row["cross"]["unsupported_claims"]) for row in crossed),
            "point_agreement": ratio(agree, points)}


def summarize(rows):
    expected_points = sum(row["points"] for row in rows)
    covered = sum(row.get("points_covered", 0) for row in rows)
    scored = sum(row["points"] for row in rows if "points_covered" in row)
    return {
        "cases": len(rows),
        "execution_errors": sum("error" in row for row in rows),
        "tool_errors": sum(row.get("tool_errors", 0) for row in rows),
        "kind_accuracy": sum(row["kind_ok"] for row in rows) / len(rows) if rows else None,
        "retried": sum(row.get("retried", False) for row in rows),
        "masked": sum(row.get("masked", False) for row in rows),
        # 미채점 항목을 정답으로 취급하지 않고 평가 범위를 명시한다.
        "expected_points": expected_points,
        "scored_points": scored,
        "covered_points": covered,
        "point_coverage": covered / expected_points if expected_points and scored == expected_points else None,
        "point_coverage_lower_bound": covered / expected_points if expected_points else None,
        "judge_errors": sum(row.get("judge_status") == "error" for row in rows),
        "unsupported_claims": sum(len(row.get("unsupported_claims", [])) for row in rows),
        "intent_match": ratio(sum(row["intent_match"] for row in rows if "intent_match" in row),
                              sum("intent_match" in row for row in rows)),
        "grounded_when_needed": ratio(sum(row["grounded"] for row in rows if row.get("needs_grounding")),
                                      sum(bool(row.get("needs_grounding")) for row in rows)),
        "cross_judge": cross_metrics(rows),
        "agent": aggregate(rows),
        "judge": aggregate([{"timings": row["judge_timings"]} for row in rows if "judge_timings" in row]),
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", choices=SETS, default="dev")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--no-judge", action="store_true", help="채점 API 없이 종류·근거·시간·토큰 기록")
    parser.add_argument("--judge-model", default="gpt-4.1-mini")
    parser.add_argument("--cross-judge", help="같은 답변을 이 모델로도 채점해 비교 (예: gpt-5.4-mini)")
    args = parser.parse_args()
    if args.repeat < 1 or (args.limit is not None and args.limit < 1):
        parser.error("--repeat와 --limit는 1 이상이어야 합니다")
    load_env()
    from negopilot_agent.graph import build_graph
    from negopilot_agent.decomposition import DecomposerSettings
    from negopilot_agent.generation import GeneratorSettings
    import openai

    gold, input_dir = SETS[args.set]
    items = json.loads(gold.read_text(encoding="utf-8"))["items"]
    if args.limit:
        items = items[:args.limit]
    d, g = DecomposerSettings(), GeneratorSettings()
    graph = build_graph()
    client = None if args.no_judge else openai.AsyncOpenAI()
    rows = []
    out_dir = ROOT / "eval_results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"pipeline-{datetime.now():%Y%m%d-%H%M%S-%f}.json"
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                capture_output=True, text=True).stdout.strip())
    metadata = {"revision": revision, "dirty": dirty, "set": args.set, "repeat": args.repeat,
                "decomposer": {"backend": d.backend, "model": d.model},
                "generator": {"backend": g.backend, "model": g.model},
                "judge_model": None if args.no_judge else args.judge_model,
                "cross_judge": None if args.no_judge else args.cross_judge}
    print(f"{args.set}: {len(items)}개 × {args.repeat}회. 모델·채점 API 비용이 발생합니다.", flush=True)
    try:
        for run in range(1, args.repeat + 1):
            for item in items:
                row = await evaluate_case(graph, item, input_dir, client, args.judge_model,
                                          None if args.no_judge else args.cross_judge)
                row["run"] = run
                rows.append(row)
                out.write_text(json.dumps({**metadata, "summary": summarize(rows), "rows": rows},
                                          ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[{run}] {row['case']}: {row.get('kind', 'error')} "
                      f"{row['timings']['total']:.2f}s, 출력 {row['timings']['output_tokens']}토큰", flush=True)
    finally:
        if client is not None:
            await client.close()
    print(json.dumps(summarize(rows), ensure_ascii=False, indent=2))
    print(f"저장: {out}")


if __name__ == "__main__":
    asyncio.run(main())
