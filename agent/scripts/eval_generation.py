"""Generation을 평가한다. MCP 서버는 필요 없다. 분해·Routing 결과를 파일에서 그대로 읽으므로 Generation만의 실력을 잰다.

| 세트 (--set) | 입력 (실제 MCP 결과) | 정답 | 쓰임 |
| --- | --- | --- | --- |
| dev (기본) | shared/fixtures/routing | eval_data/generation_gold.json | 오답을 보고 프롬프트를 고친다 |
| validation | eval_data/generation_validation/ | eval_data/generation_validation_gold.json | 점수만 본다. 오답을 보고 프롬프트를 고치지 않는다 |

검증 세트 입력은 scripts/route_generation_set.py로 실제 MCP 서버를 불러 만든다.

| 지표 | 뜻 | 모델 호출 |
| --- | --- | --- |
| 근거 포함 | 정답에 필요한 문구가 모델에게 주는 근거에 들어 있나 (must_evidence) | 없음 |
| 프롬프트 크기 | 사용자 메시지 글자 수 (토큰 ≈ 글자 ÷ 1.7) | 없음 |
| 답변 종류 | Answer.kind가 기대와 같은가 | 생성 |
| 첫 시도 통과 | 근거 검사를 한 번에 통과했나 (재생성 없음) | 생성 |
| 가림 | 재생성 후에도 근거 없는 값이 남아 [확인 필요]로 가렸나 | 생성 |
| 정답 포인트 | 정답 points 중 답변이 담은 비율 | 생성 + 채점 |
| 근거 없는 주장 | 근거에 없는 사실을 말한 개수 | 생성 + 채점 |
| 시간 | 생성 1회 시간 | 생성 |

    uv run python scripts/eval_generation.py --offline                  # 모델 없이 근거 지표만 (비용 없음)
    uv run python scripts/eval_generation.py --backend openai           # gpt-4.1-mini 생성 + 채점 (OPENAI_API_KEY)
    uv run python scripts/eval_generation.py --backend openai --repeat 3 --report eval_reports/2026-10-21_generation.md
    uv run python scripts/eval_generation.py --backend openai --no-judge --only 05 07
    uv run python scripts/eval_generation.py --backend openai --cross-judge gpt-4.1   # 같은 답변을 다른 모델로도 채점
    uv run python scripts/eval_generation.py --backend openai --repeat 3 --set validation

채점은 OpenAI(--judge-model, 기본 gpt-4.1-mini)로 한다. 생성 모델과 같으면 점수가 후할 수 있어,
--cross-judge로 같은 답변을 다른 모델로 한 번 더 채점하고 두 채점의 포인트 판정 일치율을 본다. 결과 원본은 eval_results/(git 제외)에 저장한다.
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT.parent / "shared" / "src")]

from negopilot_shared import DecompositionResult, RouteResult  # noqa: E402

from negopilot_agent.generation import AnswerGenerator, GeneratorSettings, build_evidence  # noqa: E402
from negopilot_agent.generation.answer import Answer, Evidence  # noqa: E402
from negopilot_agent.generation.prompt import build_user_message  # noqa: E402

FIXTURES = ROOT.parent / "shared" / "fixtures"
SETS = {   # 이름 → (정답 파일, 입력 폴더)
    "dev": (ROOT / "eval_data" / "generation_gold.json", FIXTURES / "routing"),
    "validation": (ROOT / "eval_data" / "generation_validation_gold.json", ROOT / "eval_data" / "generation_validation"),
}
CHARS_PER_TOKEN = 1.7   # 한국어 위주 텍스트의 대략값. 비교용이라 정확할 필요는 없다

JUDGE_SYSTEM = """\
너는 답변 채점자다. 구매 협상 Agent의 답변을 정답 포인트와 근거에 비추어 채점한다.
1. points: 정답 포인트마다 답변이 그 내용을 담았으면 covered=true. 표현이 달라도 뜻이 같으면 담은 것이다.
   숫자가 들어간 포인트는 숫자가 맞아야 담은 것이다.
2. unsupported_claims: 답변의 사실 주장(숫자, 조문 내용, 회사 정보, 판정) 중 <evidence>에 없는 것을 그대로 옮겨 적는다.
   판단·권고("법무 검토가 필요합니다", "~일 수 있습니다")와 개념 설명은 주장으로 세지 않는다.
   "[확인 필요]"로 가린 부분도 세지 않는다.
JSON으로만 답한다."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "points": {"type": "array", "items": {
            "type": "object",
            "properties": {"index": {"type": "integer"}, "covered": {"type": "boolean"}},
            "required": ["index", "covered"], "additionalProperties": False}},
        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["points", "unsupported_claims"],
    "additionalProperties": False,
}


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


def load_case(name: str, input_dir: Path = FIXTURES / "routing") -> tuple[str, DecompositionResult, RouteResult]:
    data = json.loads((input_dir / f"{name}.json").read_text(encoding="utf-8"))
    if "decomposition" in data and isinstance(data["decomposition"], dict):   # 검증 세트: 한 파일에 모두 있다
        dec = data["decomposition"]
    else:                                                                       # 개발 세트: 분해 fixture 경로
        dec = json.loads((FIXTURES / data["decomposition"]).read_text(encoding="utf-8"))["expected"]
    return data["query"], DecompositionResult.model_validate(dec), RouteResult.model_validate(data["routing"])


def evidence_of(dec: DecompositionResult, routing: RouteResult) -> list[Evidence]:
    """Generator와 같은 방식으로 근거를 만든다 (모델 호출 없음)."""
    if routing.status != "routed":
        return []
    try:
        return build_evidence(routing, {sq.id: sq.question for sq in dec.sub_queries})
    except TypeError:   # 하위 질문을 받지 않던 이전 버전과 전후 비교할 때
        return build_evidence(routing)


def offline_metrics(item: dict, dec: DecompositionResult, routing: RouteResult) -> dict:
    evidence = evidence_of(dec, routing)
    text = "\n".join(f"{e.label}\n{e.text}" for e in evidence)
    must = item["must_evidence"]
    found = [m for m in must if m in text]
    user = build_user_message(dec, evidence, []) if dec.status == "ok" else ""
    return {"evidence_count": len(evidence), "must_evidence": len(must), "must_found": len(found),
            "missing_evidence": [m for m in must if m not in found], "prompt_chars": len(user)}


async def judge(client, model: str, item: dict, answer: Answer) -> dict:
    points = "\n".join(f"{i}. {p}" for i, p in enumerate(item["points"]))
    evidence = "\n\n".join(f"[{e.id}] {e.label}\n{e.text}" for e in answer.evidence) or "(근거 없음)"
    user = f"<points>\n{points}\n</points>\n\n<evidence>\n{evidence}\n</evidence>\n\n<answer>\n{answer.text}\n</answer>"
    response = await client.chat.completions.create(
        model=model, temperature=0,
        messages=[{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "GenerationJudgement", "schema": JUDGE_SCHEMA, "strict": True}},
    )
    result = json.loads(response.choices[0].message.content or "{}")
    covered = {p["index"] for p in result.get("points", []) if p.get("covered")}
    covered &= set(range(len(item["points"])))
    return {"points_covered": len(covered), "covered_points": sorted(covered),
            "uncovered_points": [p for i, p in enumerate(item["points"]) if i not in covered],
            "unsupported_claims": result.get("unsupported_claims", [])}


async def run_case(gen: AnswerGenerator | None, judge_client, judge_model: str, item: dict,
                   input_dir: Path = FIXTURES / "routing", cross_model: str | None = None) -> dict:
    query, dec, routing = load_case(item["fixture"], input_dir)
    row = {"fixture": item["fixture"], "query": query, **offline_metrics(item, dec, routing)}
    if gen is None:
        return row
    start = time.perf_counter()
    answer = await gen.generate(dec, routing)
    row["seconds"] = round(time.perf_counter() - start, 2)
    v = answer.verification
    row.update({"kind": answer.kind, "kind_ok": answer.kind == item["kind"], "used_model": v is not None,
                "first_try_ok": bool(v and v.attempts == 1 and v.ok), "retried": bool(v and v.attempts > 1),
                "masked": bool(v and v.masked), "points": len(item["points"]), "answer": answer.text,
                "citations": [c.label for c in answer.citations]})
    if judge_client is not None and item["points"] and answer.kind == "answer":
        row.update(await judge(judge_client, judge_model, item, answer))
        if cross_model:
            row["cross"] = await judge(judge_client, cross_model, item, answer)
    return row


def pct(n: float, d: float) -> str:
    return f"{n / d * 100:.0f}% ({n:g}/{d:g})" if d else "-"


def cross_summary(rows: list[dict]) -> list[str]:
    """같은 답변을 두 모델이 채점한 결과 비교. 일치율 = (답변, 정답 포인트) 짝 중 두 채점이 같게 판정한 비율."""
    total = sum(r["points"] for r in rows)
    agree = sum(r["points"] - len(set(r["covered_points"]) ^ set(r["cross"]["covered_points"])) for r in rows)
    return [f"| 정답 포인트 커버 (교차 채점) | {pct(sum(r['cross']['points_covered'] for r in rows), total)} |",
            f"| 근거 없는 주장 (교차 채점, 답변당) | {statistics.mean(len(r['cross']['unsupported_claims']) for r in rows):.2f}개 |",
            f"| 포인트 판정 일치율 (두 채점) | {pct(agree, total)} |"]


def summarize(runs: list[list[dict]]) -> str:
    rows = [r for run in runs for r in run]
    first = runs[0]
    lines = ["| 지표 | 값 |", "| --- | --- |"]
    must = sum(r["must_evidence"] for r in first)
    lines.append(f"| 근거 포함 (must_evidence) | {pct(sum(r['must_found'] for r in first), must)} |")
    chars = [r["prompt_chars"] for r in first if r["prompt_chars"]]
    if chars:
        lines.append(f"| 프롬프트 크기 (평균 / 최대) | {statistics.mean(chars):,.0f}자 / {max(chars):,}자 "
                     f"(≈ {statistics.mean(chars) / CHARS_PER_TOKEN:,.0f} / {max(chars) / CHARS_PER_TOKEN:,.0f} 토큰) |")
    if "kind" in rows[0]:
        model_rows = [r for r in rows if r["used_model"]]
        lines.append(f"| 답변 종류 일치 | {pct(sum(r['kind_ok'] for r in rows), len(rows))} |")
        lines.append(f"| 첫 시도 근거 검사 통과 | {pct(sum(r['first_try_ok'] for r in model_rows), len(model_rows))} |")
        lines.append(f"| 재생성 | {pct(sum(r['retried'] for r in model_rows), len(model_rows))} |")
        lines.append(f"| [확인 필요]로 가림 | {pct(sum(r['masked'] for r in model_rows), len(model_rows))} |")
        judged = [r for r in rows if "points_covered" in r]
        if judged:
            lines.append(f"| 정답 포인트 커버 | {pct(sum(r['points_covered'] for r in judged), sum(r['points'] for r in judged))} |")
            lines.append(f"| 근거 없는 주장 (답변당 평균) | {statistics.mean(len(r['unsupported_claims']) for r in judged):.2f}개 |")
            crossed = [r for r in judged if "cross" in r]
            if crossed:
                lines += cross_summary(crossed)
        if model_rows:
            secs = [r["seconds"] for r in model_rows]
            lines.append(f"| 생성 시간 (평균 / 최대) | {statistics.mean(secs):.1f}초 / {max(secs):.1f}초 |")
    lines += ["", "| fixture | 근거 포함 | 프롬프트 | 종류 | 첫 시도 | 포인트 | 근거 없는 주장 | 시간 |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name in [r["fixture"] for r in first]:
        same = [r for r in rows if r["fixture"] == name]
        r0 = same[0]
        cells = [name, f"{r0['must_found']}/{r0['must_evidence']}" if r0["must_evidence"] else "-",
                 f"{r0['prompt_chars']:,}자" if r0["prompt_chars"] else "-"]
        if "kind" in r0:
            cells.append(f"{sum(r['kind_ok'] for r in same)}/{len(same)}")
            cells.append(f"{sum(r['first_try_ok'] for r in same)}/{len(same)}" if r0["used_model"] else "-")
            judged = [r for r in same if "points_covered" in r]
            cells.append(f"{sum(r['points_covered'] for r in judged)}/{sum(r['points'] for r in judged)}" if judged else "-")
            cells.append(f"{sum(len(r['unsupported_claims']) for r in judged)}" if judged else "-")
            cells.append(f"{statistics.mean(r['seconds'] for r in same):.1f}초" if r0["used_model"] else "-")
        else:
            cells += ["-"] * 5
        lines.append("| " + " | ".join(cells) + " |")
    missing = {r["fixture"]: r["missing_evidence"] for r in first if r["missing_evidence"]}
    if missing:
        lines += ["", "근거에 빠진 문구: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in missing.items())]
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="모델 없이 근거 지표만 계산 (비용 없음)")
    parser.add_argument("--set", choices=list(SETS), default="dev",
                        help="dev: 개발 세트(기본), validation: 검증 세트 (오답을 보고 프롬프트를 고치지 않는다)")
    parser.add_argument("--backend", choices=["openai", "anthropic"], help="기본: 설정값")
    parser.add_argument("--model", help="기본: 백엔드별 기본 모델")
    parser.add_argument("--repeat", type=int, default=1, help="같은 fixture를 N회 생성해 합산 (기본 1)")
    parser.add_argument("--only", nargs="*", help="fixture 번호나 이름 일부 (예: 05 07)")
    parser.add_argument("--no-judge", action="store_true", help="정답 포인트·근거 없는 주장 채점을 건너뜀")
    parser.add_argument("--judge-model", default="gpt-4.1-mini")
    parser.add_argument("--cross-judge", help="같은 답변을 이 모델로도 채점해 비교 (예: gpt-4.1)")
    parser.add_argument("--report", help="요약 표를 이 경로의 Markdown 파일로도 저장")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", skip=("NEGOPILOT_GENERATOR_MODEL",) if args.backend else ())
    if args.backend:
        os.environ["NEGOPILOT_GENERATOR_BACKEND"] = args.backend
    if args.model:
        os.environ["NEGOPILOT_GENERATOR_MODEL"] = args.model

    gold, input_dir = SETS[args.set]
    items = json.loads(gold.read_text(encoding="utf-8"))["items"]
    if args.only:
        items = [i for i in items if any(key in i["fixture"] for key in args.only)]
    if not items:
        print("해당하는 fixture가 없다", file=sys.stderr)
        return 1

    gen, judge_client, settings = None, None, None
    if not args.offline:
        settings = GeneratorSettings()
        gen = AnswerGenerator(settings=settings)
        if not args.no_judge:
            import openai
            judge_client = openai.AsyncOpenAI()
    head = "오프라인 (모델 호출 없음)" if args.offline else (
        f"생성 {settings.backend} · {settings.model} · 채점 {'없음' if args.no_judge else args.judge_model}"
        + (f" + 교차 {args.cross_judge}" if args.cross_judge and not args.no_judge else ""))
    head += f" · {args.set} 세트"
    print(f"{head} · fixtures {len(items)}개 × {1 if args.offline else args.repeat}회\n")

    runs = []
    for n in range(1 if args.offline else args.repeat):
        rows = []
        for item in items:
            row = await run_case(gen, judge_client, args.judge_model, item, input_dir,
                                 cross_model=None if args.no_judge else args.cross_judge)
            rows.append(row)
            if not args.offline:
                print(f"[{n + 1}] {item['fixture']}: {row['kind']} · 첫 시도 {'통과' if row['first_try_ok'] else '-'}"
                      f" · {row.get('points_covered', '-')}/{row['points']} · {row['seconds']}초")
        runs.append(rows)

    summary = summarize(runs)
    print("\n" + summary)
    out_dir = ROOT / "eval_results"
    out_dir.mkdir(exist_ok=True)
    tag = "offline" if args.offline else f"{settings.backend}_{settings.model.replace(':', '-')}"
    tag += "" if args.set == "dev" else f"_{args.set}"
    out = out_dir / f"generation_{datetime.now():%Y%m%d_%H%M%S}_{tag}.json"
    out.write_text(json.dumps({"mode": tag, "set": args.set, "judge_model": None if args.no_judge or args.offline else args.judge_model,
                               "cross_judge": None if args.no_judge or args.offline else args.cross_judge,
                               "runs": runs}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n원본: {out.relative_to(ROOT)}")
    if args.report:
        Path(args.report).write_text(f"# Generation 평가 ({head})\n\n{summary}\n", encoding="utf-8")
        print(f"리포트: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
