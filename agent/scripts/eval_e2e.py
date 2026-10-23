"""질문부터 최종 답변까지 끝까지 평가한다 (분해 → 실제 MCP Routing → 생성).

eval_generation.py는 저장된 Routing 결과로 Generation만 재고, eval_decomposition.py는 분해만 잰다.
이 스크립트는 사용자가 실제로 받는 답변을 같은 정답 포인트로 채점한다. 분해가 조금 달라도 답이 맞을 수 있고,
단계마다 맞아도 이어 붙이면 틀릴 수 있어 둘을 함께 본다.

먼저 MCP 서버 두 개를 띄운다 (README 빠른 시작). 모델을 부르므로 비용이 든다.

    uv run python scripts/eval_e2e.py --set validation
    uv run python scripts/eval_e2e.py --set dev --repeat 2 --cross-judge gpt-5.4-mini

정답과 채점 방식은 eval_generation.py와 같다 (같은 JUDGE_SYSTEM, 같은 정답 파일). 결과 원본은 eval_results/에 저장한다.
"""

import argparse
import asyncio
import importlib.util
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT.parent / "shared" / "src")]

_spec = importlib.util.spec_from_file_location("eval_generation", Path(__file__).resolve().parent / "eval_generation.py")
eg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eg)

from negopilot_agent import timing  # noqa: E402

NO_GROUNDING = {"general_knowledge", "out_of_scope"}


async def run_case(app, judge_client, judge_model: str, item: dict, input_dir: Path,
                   cross_model: str | None = None) -> dict:
    query, expected_dec, _ = eg.load_case(item["fixture"], input_dir)
    row = {"fixture": item["fixture"], "query": query, "points": len(item["points"])}
    start = time.perf_counter()
    try:
        with timing.collect() as t:
            state = await app.ainvoke({"query": query})
    except Exception as err:   # 한 질문의 오류로 전체가 멈추지 않게 오답으로 기록한다
        row.update({"error": f"{type(err).__name__}: {err}"[:300], "kind_ok": False, "seconds": round(time.perf_counter() - start, 2)})
        return row
    answer, dec = state["answer"], state["decomposition"]
    summary = t.summary()
    row.update({
        "seconds": round(summary["total"], 2), "cost_usd": summary.get("cost_usd"),
        "kind": answer.kind, "kind_ok": answer.kind == item["kind"], "grounded": answer.grounded,
        "intents": [s.intent for s in dec.sub_queries],
        "expected_intents": [s.intent for s in expected_dec.sub_queries],
        "routing": [o.status for o in state["routing"].outcomes] if state.get("routing") else [],
        "answer": answer.text, "citations": [c.label for c in answer.citations],
    })
    if judge_client is not None and item["points"] and answer.kind == "answer":
        row.update(await eg.judge(judge_client, judge_model, item, answer))
        if cross_model:
            row["cross"] = await eg.judge(judge_client, cross_model, item, answer)
    return row


def summarize(rows: list[dict]) -> str:
    ok = [r for r in rows if "error" not in r]
    pct = eg.pct
    lines = ["| 지표 | 값 |", "| --- | --- |",
             f"| 실행 오류 | {pct(len(rows) - len(ok), len(rows))} |",
             f"| 답변 종류 일치 | {pct(sum(r['kind_ok'] for r in rows), len(rows))} |",
             f"| 분해 intent가 정답과 같음 | {pct(sum(r['intents'] == r['expected_intents'] for r in ok), len(ok))} |"]
    # 개념 설명·범위 밖은 근거 없이 답하는 게 맞으므로, 근거가 필요한 답변만 센다
    answers = [r for r in ok if r.get("kind") == "answer" and not set(r["intents"]) <= NO_GROUNDING]
    if answers:
        lines.append(f"| 근거 표시 있음 (근거가 필요한 답변) | {pct(sum(r['grounded'] for r in answers), len(answers))} |")
    judged = [r for r in ok if "points_covered" in r]
    if judged:
        lines.append(f"| 정답 포인트 커버 | {pct(sum(r['points_covered'] for r in judged), sum(r['points'] for r in judged))} |")
        lines.append(f"| 근거 없는 주장 (답변당) | {statistics.mean(len(r['unsupported_claims']) for r in judged):.2f}개 |")
        crossed = [r for r in judged if "cross" in r]
        if crossed:
            lines += eg.cross_summary(crossed)
    if ok:
        secs = [r["seconds"] for r in ok]
        lines.append(f"| 시간 (평균 / 최대) | {statistics.mean(secs):.1f}초 / {max(secs):.1f}초 |")
        costs = [r["cost_usd"] for r in ok if r.get("cost_usd") is not None]
        if costs:
            lines.append(f"| 비용 (질문당 평균) | ${statistics.mean(costs):.4f} |")
    lines += ["", "| 질문 | 종류 | 분해 intent | 포인트 | 근거 없는 주장 | 시간 |", "| --- | --- | --- | --- | --- | --- |"]
    for name in dict.fromkeys(r["fixture"] for r in rows):
        same = [r for r in rows if r["fixture"] == name]
        good = [r for r in same if "error" not in r]
        judged_same = [r for r in good if "points_covered" in r]
        lines.append("| " + " | ".join([
            name, f"{sum(r['kind_ok'] for r in same)}/{len(same)}",
            f"{sum(r['intents'] == r['expected_intents'] for r in good)}/{len(good)}" if good else "오류",
            f"{sum(r['points_covered'] for r in judged_same)}/{sum(r['points'] for r in judged_same)}" if judged_same else "-",
            f"{sum(len(r['unsupported_claims']) for r in judged_same)}" if judged_same else "-",
            f"{statistics.mean(r['seconds'] for r in good):.1f}초" if good else "-"]) + " |")
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", choices=list(eg.SETS), default="validation", help="기본: validation")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--only", nargs="*", help="fixture 이름 일부 (예: gv01 07)")
    parser.add_argument("--judge-model", default="gpt-4.1-mini")
    parser.add_argument("--cross-judge", help="같은 답변을 이 모델로도 채점 (예: gpt-5.4-mini)")
    args = parser.parse_args()

    eg.load_dotenv(ROOT / ".env")
    gold_path, input_dir = eg.SETS[args.set]
    items = json.loads(gold_path.read_text(encoding="utf-8"))["items"]
    if args.only:
        items = [i for i in items if any(key in i["fixture"] for key in args.only)]

    import openai
    from negopilot_agent.decomposition import DecomposerSettings
    from negopilot_agent.generation import GeneratorSettings
    from negopilot_agent.graph import build_graph
    d, g = DecomposerSettings(), GeneratorSettings()
    app, judge_client = build_graph(), openai.AsyncOpenAI()
    head = (f"분해 {d.backend}·{d.model} · 생성 {g.backend}·{g.model} · 채점 {args.judge_model}"
            + (f" + 교차 {args.cross_judge}" if args.cross_judge else "") + f" · {args.set} {len(items)}개 × {args.repeat}회")
    print(head + "\n")

    rows = []
    for n in range(args.repeat):
        for item in items:
            row = await run_case(app, judge_client, args.judge_model, item, input_dir, args.cross_judge)
            rows.append(row | {"run": n + 1})
            print(f"[{n + 1}] {item['fixture']}: {row.get('kind', '오류')} · intent {'일치' if row.get('intents') == row.get('expected_intents') else '다름'}"
                  f" · {row.get('points_covered', '-')}/{row['points']} · {row.get('seconds', '-')}초", flush=True)

    summary = summarize(rows)
    print("\n" + summary)
    out_dir = ROOT / "eval_results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"e2e_{datetime.now():%Y%m%d_%H%M%S}_{args.set}.json"
    out.write_text(json.dumps({"head": head, "set": args.set, "judge_model": args.judge_model,
                               "cross_judge": args.cross_judge, "rows": rows}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"\n원본: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
