"""검토 루프 전후 비교. 같은 분해 결과로 review_rounds=0(예전 일직선)과 1(검토 루프)을 돌려 답을 비교한다.

MCP 서버와 모델 키가 필요하다 (ask.py와 같다). 분해는 질문마다 한 번만 해서 두 실행에 똑같이 넣는다.
그래서 차이는 검토 루프(와 생성 모델의 흔들림)에서만 나온다.

    uv run python scripts/eval_review_loop.py                       # 루프가 걸릴 만한 질문 세트 (trigger)
    uv run python scripts/eval_review_loop.py --set validation      # Generation 검증 세트 15문항 (저장된 분해 사용)
    uv run python scripts/eval_review_loop.py --set validation --limit 5

결과: eval_results/review-loop-<세트>-<시각>.json 과 같은 이름의 .md (표)
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT.parent / "shared" / "src"), str(ROOT / "scripts")]

from ask import load_env  # noqa: E402

# 지역·생산능력·인증 조건 때문에 0건이 나기 쉬운 질문, 판단에 필요한 법 조건을 따로 묻지 않은 질문. 끝의 둘은 대조군
TRIGGER = [
    "제주 지역 프레스 협력사 찾아줘",
    "강원도에 있는 알루미늄 다이캐스팅 업체 보여줘",
    "월 생산능력 50만 개 이상인 사출 협력사 찾아줘",
    "전남에 있는 IATF 인증 단조 업체 알려줘",
    "SUP-0003 결제 조건이 하도급법상 괜찮은지 관련 조문 번호까지 알려줘",
    "RFQ-2026-001 1위 협력사에 결제 75일로 제안하면 하도급법 몇 조에 걸려?",
    "SUP-0003이 원자재 가격이 올라서 단가를 8% 올려 달래. 받아 줘야 해?",
    "SUP-0012한테 납품 단가를 5% 깎자고 해도 돼?",
    "SUP-0010 정보 알려줘",
    "하도급대금 지급 기한은 며칠이야?",
]


class Fixed:
    """처음(원래 질문) 분해는 정해 둔 결과를 돌려주고, 그 뒤(검토자의 보충 질문)는 실제 분해기에 맡긴다."""

    def __init__(self, text: str):
        self.text = text
        self.real = None

    async def decompose(self, query: str, history: list[dict] | None = None, **kwargs):
        from negopilot_shared import DecompositionResult
        from negopilot_agent.decomposition import QueryDecomposer
        if self.text is not None:
            text, self.text = self.text, None
            return DecompositionResult.model_validate_json(text)
        self.real = self.real or QueryDecomposer()
        return await self.real.decompose(query, history, **kwargs)


async def run(query: str, decomposition_json: str, rounds: int) -> dict:
    from negopilot_agent import timing
    from negopilot_agent.graph import build_graph

    graph = build_graph(decomposer=Fixed(decomposition_json), review_rounds=rounds)
    with timing.collect() as t:
        state = await graph.ainvoke({"query": query})
    answer = state["answer"]
    review = state.get("review")
    v = answer.verification
    return {
        "kind": answer.kind,
        "masked": bool(v and v.masked),
        "ungrounded": (v.ungrounded_numbers + v.ungrounded_articles) if v else [],
        "citations": len(answer.citations),
        "notices": answer.notices,
        "text": answer.text,
        "outcomes": [o.status for o in state["routing"].outcomes] if state.get("routing") else [],
        "sub_queries": [sq.question for sq in state["decomposition"].sub_queries],
        "review": [[{"kind": a.kind, "resolved": a.resolved, "detail": a.detail} for a in r.actions]
                   for r in review.rounds] if review else [],
        "timings": t.summary(),
    }


async def cases(name: str, limit: int | None) -> list[tuple[str, str, str]]:
    """(id, 질문, 분해 JSON). trigger는 지금 분해하고, validation은 저장된 분해를 쓴다."""
    if name == "validation":
        from eval_generation import SETS, load_case
        gold, input_dir = SETS["validation"]
        items = json.loads(gold.read_text(encoding="utf-8"))["items"]
        out = []
        for item in items[:limit]:
            query, dec, _ = load_case(item["fixture"], input_dir)
            out.append((item["fixture"], query, dec.model_dump_json(by_alias=True)))
        return out
    from negopilot_agent.decomposition import QueryDecomposer
    decomposer = QueryDecomposer()
    out = []
    for i, query in enumerate(TRIGGER[:limit], 1):
        dec = await decomposer.decompose(query)
        out.append((f"t{i:02d}", query, dec.model_dump_json(by_alias=True)))
    return out


def summary(rows: list[dict]) -> dict:
    def mode(key: str) -> dict:
        rs = [r[key] for r in rows]
        return {
            "answers": sum(r["kind"] == "answer" for r in rs),
            "no_result": sum(r["kind"] == "no_result" for r in rs),
            "masked": sum(r["masked"] for r in rs),
            "llm_calls": sum(r["timings"].get("llm_calls", 0) for r in rs),
            "tool_calls": sum(r["timings"].get("tool_calls", 0) for r in rs),
            "seconds": round(sum(r["timings"].get("total", 0) for r in rs), 2),
            "cost_usd": round(sum(r["timings"].get("cost_usd", 0) for r in rs), 5),
        }
    return {"cases": len(rows), "before": mode("before"), "after": mode("after"),
            "triggered": sum(bool(r["after"]["review"]) for r in rows),
            "follow_up": sum(any(a["kind"] == "follow_up" for rd in r["after"]["review"] for a in rd) for r in rows),
            "resolved": sum(any(a["resolved"] for rd in r["after"]["review"] for a in rd) for r in rows)}


def markdown(name: str, rows: list[dict], s: dict) -> str:
    b, a = s["before"], s["after"]
    lines = [f"# 검토 루프 전후 비교 ({name}, {s['cases']}문항)", "",
             "| 지표 | 루프 없음 | 검토 루프 |", "| --- | --- | --- |"]
    for key, label in [("answers", "답변(answer)"), ("no_result", "결과 없음"), ("masked", "[확인 필요] 가림"),
                       ("llm_calls", "LLM 호출"), ("tool_calls", "도구 호출"), ("seconds", "총 시간(초)"),
                       ("cost_usd", "비용(USD)")]:
        lines.append(f"| {label} | {b[key]} | {a[key]} |")
    lines += ["", f"루프가 보강을 시도한 문항 {s['triggered']}개 (모델 검토자 보충 {s['follow_up']}개), "
              f"그중 보강에 성공한 문항 {s['resolved']}개", "",
              "| id | 질문 | 전 | 후 | 보강 |", "| --- | --- | --- | --- | --- |"]
    for r in rows:
        def cell(x):
            return f"{x['kind']}{' (가림)' if x['masked'] else ''}"
        acts = "; ".join(f"{a['kind']}{'✓' if a['resolved'] else '✗'}" for rd in r["after"]["review"] for a in rd) or "-"
        lines.append(f"| {r['id']} | {r['query']} | {cell(r['before'])} | {cell(r['after'])} | {acts} |")
    return "\n".join(lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", choices=["trigger", "validation"], default="trigger")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    load_env()

    rows = []
    for cid, query, dec in await cases(args.set, args.limit):
        before = await run(query, dec, 0)
        after = await run(query, dec, 1)
        rows.append({"id": cid, "query": query, "before": before, "after": after})
        print(f"{cid} {before['kind']}{'*' if before['masked'] else ''} → {after['kind']}{'*' if after['masked'] else ''}"
              f" {[a['kind'] for rd in after['review'] for a in rd]}  {query}", flush=True)

    s = summary(rows)
    out = ROOT / "eval_results"
    out.mkdir(exist_ok=True)
    stem = f"review-loop-{args.set}-{datetime.now():%Y%m%d-%H%M%S}"
    (out / f"{stem}.json").write_text(json.dumps({"summary": s, "rows": rows}, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    (out / f"{stem}.md").write_text(markdown(args.set, rows, s), encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False, indent=2))
    print(f"→ {out / stem}.json, .md")


if __name__ == "__main__":
    asyncio.run(main())
