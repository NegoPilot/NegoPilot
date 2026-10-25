"""실제 그래프의 최종 품질·토큰·시간 평가. MCP 서버와 모델 키가 필요하다.

uv run python scripts/eval_pipeline.py --set dev --limit 3 --repeat 3
uv run python scripts/eval_pipeline.py --set validation --no-judge
uv run python scripts/eval_pipeline.py --set validation --cross-judge gpt-5.4-mini   # 같은 답변을 다른 모델로도 채점
uv run python scripts/eval_pipeline.py --set validation --repeat 6 --compare eval_results/pipeline-<이전>.json

--repeat 2 이상이면 회차별 값과 표준편차·표준오차를 낸다. --compare는 지표 차이가 두 결과의 표준오차를
합친 폭의 2배보다 작으면 "흔들림 범위"로 표시한다. 같은 세트·모델로 잰 결과끼리 비교한다.

Generation 평가의 질문과 정답을 재사용하되 저장된 분해·Routing을 실행 입력으로
쓰지 않는다. 채점은 별도 호출이며 실제 Agent의 토큰/시간과 분리한다.
"""

import argparse
import asyncio
import json
import statistics
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


def quality(rows):
    """품질 비율. 전체 합계와 회차별 값(spread)이 같은 기준을 쓴다."""
    expected_points = sum(row["points"] for row in rows)
    covered = sum(row.get("points_covered", 0) for row in rows)
    scored = sum(row["points"] for row in rows if "points_covered" in row)
    return {
        "kind_accuracy": sum(row["kind_ok"] for row in rows) / len(rows) if rows else None,
        # 미채점 항목을 정답으로 취급하지 않고 평가 범위를 명시한다.
        "expected_points": expected_points,
        "scored_points": scored,
        "covered_points": covered,
        "point_coverage": covered / expected_points if expected_points and scored == expected_points else None,
        "point_coverage_lower_bound": covered / expected_points if expected_points else None,
        "intent_match": ratio(sum(row["intent_match"] for row in rows if "intent_match" in row),
                              sum("intent_match" in row for row in rows)),
        "grounded_when_needed": ratio(sum(row["grounded"] for row in rows if row.get("needs_grounding")),
                                      sum(bool(row.get("needs_grounding")) for row in rows)),
    }


# 회차별로 흩어짐을 보는 지표 → 보고서 이름
SPREAD_METRICS = {"point_coverage": "정답 포인트 커버", "kind_accuracy": "답변 종류 일치",
                  "intent_match": "분해 intent 일치", "grounded_when_needed": "근거 표시"}


def spread(rows):
    """회차별 값과 흩어짐. 변경 전후 차이가 이 폭보다 작으면 소음으로 본다 (eval_generation과 같은 방식).

    회차가 2개 이상이고 값이 있는 회차가 2개 이상인 지표만 낸다. 채점이 빠진 회차는 point_coverage가 None이라 빠진다.
    """
    runs = {}
    for row in rows:
        runs.setdefault(row.get("run", 1), []).append(row)
    if len(runs) < 2:
        return {}
    result = {}
    for key in SPREAD_METRICS:
        values = [value for run in sorted(runs) if (value := quality(runs[run])[key]) is not None]
        if len(values) < 2:
            continue
        stdev = statistics.stdev(values)
        # 평균의 표준오차. 회차를 늘리면 줄어든다 (n회 → 1/√n)
        result[key] = {"per_run": values, "mean": statistics.mean(values),
                       "stdev": stdev, "sem": stdev / len(values) ** 0.5}
    return result


def by_case(rows):
    """문항별로 회차를 모은다. 매번 같은 문항이 틀리는지 본다."""
    cases = {}
    for row in rows:
        case = cases.setdefault(row["case"], {"runs": 0, "kind_ok": 0, "intent_match": 0, "points": 0,
                                              "covered": 0, "retried": 0, "tool_errors": 0, "errors": 0})
        case["runs"] += 1
        case["kind_ok"] += row["kind_ok"]
        case["intent_match"] += row.get("intent_match", False)
        case["points"] += row["points"]
        case["covered"] += row.get("points_covered", 0)
        case["retried"] += row.get("retried", False)
        case["tool_errors"] += row.get("tool_errors", 0)
        case["errors"] += "error" in row
    return cases


def summarize(rows):
    return {
        "cases": len(rows),
        "execution_errors": sum("error" in row for row in rows),
        "tool_errors": sum(row.get("tool_errors", 0) for row in rows),
        "retried": sum(row.get("retried", False) for row in rows),
        "masked": sum(row.get("masked", False) for row in rows),
        **quality(rows),
        "judge_errors": sum(row.get("judge_status") == "error" for row in rows),
        "unsupported_claims": sum(len(row.get("unsupported_claims", [])) for row in rows),
        "spread": spread(rows),
        "cross_judge": cross_metrics(rows),
        "agent": aggregate(rows),
        "judge": aggregate([{"timings": row["judge_timings"]} for row in rows if "judge_timings" in row]),
    }


def pct(value):
    return "-" if value is None else f"{100 * value:.1f}%"


def compare(base, summary):
    """이전 결과와 지표별 차이. 두 결과의 표준오차를 합친 폭의 2배보다 작으면 흔들림 범위로 본다."""
    result = {}
    for key in SPREAD_METRICS:
        before, after = base["summary"].get(key), summary.get(key)
        if before is None or after is None:
            continue
        sems = [s[key]["sem"] for s in (base["summary"].get("spread") or {}, summary["spread"]) if key in s]
        noise = 2 * sum(sem ** 2 for sem in sems) ** 0.5 if len(sems) == 2 else None
        diff = after - before
        verdict = "흔들림 모름" if noise is None else ("흔들림 범위" if abs(diff) <= noise else
                                                     ("개선" if diff > 0 else "하락"))
        result[key] = {"before": before, "after": after, "diff": diff, "noise": noise, "verdict": verdict}
    return result


def report(metadata, summary, rows, base=None, base_path=None):
    """사람이 읽는 Markdown 보고서. 원본 수치는 JSON 결과 파일에 있다."""
    answers = sum(row.get("kind") == "answer" for row in rows)
    total = summary["agent"].get("total", {})
    lines = [f"# 전체 흐름 평가: {metadata['set']}",
             "",
             f"- 커밋 `{metadata['revision'][:7]}`{' (dirty)' if metadata['dirty'] else ''} · "
             f"{len(by_case(rows))}문항 × {metadata['repeat']}회",
             f"- 분해 {metadata['decomposer']['model']} · 생성 {metadata['generator']['model']} · "
             f"채점 {metadata['judge_model'] or '없음'}",
             "",
             "| 지표 | 값 |", "| --- | --- |",
             f"| 정답 포인트 커버 | {pct(summary['point_coverage'])} "
             f"({summary['covered_points']}/{summary['expected_points']}) |",
             f"| 답변 종류 일치 | {pct(summary['kind_accuracy'])} |",
             f"| 분해 intent 일치 | {pct(summary['intent_match'])} |",
             f"| 근거 표시 (근거가 필요한 답변) | {pct(summary['grounded_when_needed'])} |",
             f"| 근거 없는 주장 (답변당 평균) | "
             f"{summary['unsupported_claims'] / answers:.2f}개 |" if answers else "| 근거 없는 주장 | - |",
             f"| 재생성 / [확인 필요]로 가림 | {summary['retried']} / {summary['masked']} |",
             f"| 실행 오류 / 도구 오류 / 채점 오류 | {summary['execution_errors']} / {summary['tool_errors']} / "
             f"{summary['judge_errors']} |"]
    if total:
        lines.append(f"| 질문당 시간 (평균 / p90) | {total['mean']:.2f}초 / {total['p90']:.2f}초 |")
    cost = summary["agent"].get("cost_usd")
    if cost:
        lines.append(f"| 질문당 비용 (평균) | ${cost['mean']:.4f} |")
    for key, label in SPREAD_METRICS.items():
        if key in summary["spread"]:
            s = summary["spread"][key]
            lines.append(f"| {label} 회차별 | {', '.join(pct(v) for v in s['per_run'])} "
                         f"(표준편차 {100 * s['stdev']:.1f}pp, 표준오차 {100 * s['sem']:.1f}pp) |")

    if base is not None:
        changes = compare(base, summary)
        lines += ["", f"## 이전 결과와 비교 ({Path(base_path).name}, 커밋 `{base.get('revision', '')[:7]}`)", ""]
        if (base.get("set"), base.get("decomposer"), base.get("generator")) != \
                (metadata["set"], metadata["decomposer"], metadata["generator"]):
            lines += ["**주의: 세트나 모델이 달라 같은 기준 비교가 아니다.**", ""]
        lines += ["| 지표 | 이전 | 지금 | 차이 | 흔들림 폭 | 판단 |", "| --- | --- | --- | --- | --- | --- |"]
        for key, c in changes.items():
            noise = "-" if c["noise"] is None else f"±{100 * c['noise']:.1f}pp"
            lines.append(f"| {SPREAD_METRICS[key]} | {pct(c['before'])} | {pct(c['after'])} | "
                         f"{100 * c['diff']:+.1f}pp | {noise} | {c['verdict']} |")

    lines += ["", "## 문항별", "",
              "| 문항 | 종류 일치 | intent 일치 | 포인트 | 재생성 | 도구 오류 | 실행 오류 |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for name, c in by_case(rows).items():
        lines.append(f"| {name} | {c['kind_ok']}/{c['runs']} | {c['intent_match']}/{c['runs']} | "
                     f"{c['covered']}/{c['points']} | {c['retried']} | {c['tool_errors']} | {c['errors']} |")
    return "\n".join(lines) + "\n"


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", choices=SETS, default="dev")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--no-judge", action="store_true", help="채점 API 없이 종류·근거·시간·토큰 기록")
    parser.add_argument("--judge-model", default="gpt-4.1-mini")
    parser.add_argument("--cross-judge", help="같은 답변을 이 모델로도 채점해 비교 (예: gpt-5.4-mini)")
    parser.add_argument("--report", help="Markdown 보고서를 이 경로에도 저장")
    parser.add_argument("--compare", help="이전 결과 JSON(eval_results/pipeline-*.json)과 지표를 비교")
    args = parser.parse_args()
    if args.repeat < 1 or (args.limit is not None and args.limit < 1):
        parser.error("--repeat와 --limit는 1 이상이어야 합니다")
    # 비용을 쓰기 전에 비교 대상 파일부터 읽어 본다
    base = json.loads(Path(args.compare).read_text(encoding="utf-8")) if args.compare else None
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
    text = report(metadata, summarize(rows), rows, base, args.compare)
    print("\n" + text)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
        print(f"보고서: {args.report}")
    print(f"저장: {out}")


if __name__ == "__main__":
    asyncio.run(main())
