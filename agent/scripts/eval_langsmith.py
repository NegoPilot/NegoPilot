"""Generation 평가 세트를 LangSmith 데이터셋으로 올리고, 실제 그래프를 실험(experiment)으로 돌린다.

    uv run python scripts/eval_langsmith.py upload --set validation          # 데이터셋 만들기·동기화 (모델 비용 없음)
    uv run python scripts/eval_langsmith.py run --set validation             # 실험 1회 (모델·채점 비용 발생)
    uv run python scripts/eval_langsmith.py run --set validation --repeat 3 --no-judge

필요한 것: agent/.env의 LANGSMITH_API_KEY, 모델 키, 실행 중인 MCP 서버 두 개 (eval_pipeline.py와 같다).
run은 이 실행 동안 LANGSMITH_TRACING을 켜서 예제마다 노드·LLM·MCP 호출이 함께 기록된다.

- 데이터셋: 예제 하나 = 정답 파일의 항목 하나. inputs는 질문만, outputs는 정답(kind, points, must_evidence,
  expected_intents)이다. 예제는 metadata.fixture로 찾아 같으면 두고, 바뀌면 고치고, 없으면 만든다.
  로컬에서 지운 항목은 LangSmith에서 지우지 않고 경고만 한다.
- 실험: 대상(target)은 그래프만 실행한다. 채점은 평가자(evaluator)에서 따로 해서 실험의 시간·토큰에 섞이지 않는다.
  평가자 기준은 eval_pipeline.py와 같다 (종류 일치, 분해 intent 일치, 근거 표시, 필수 근거, 재시도·마스킹, 정답 포인트).
- 실험 이름은 '<세트>-<커밋 7자리>[-dirty]'로 시작한다. 개선 전후는 LangSmith의 실험 비교 화면에서 본다.
- 검증 세트 실험의 오답을 보고 프롬프트를 고치지 않는다. 프롬프트 수정은 dev 세트로 한다.
"""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT.parent / "shared" / "src"), str(ROOT / "scripts")]

from ask import load_env  # noqa: E402
from eval_generation import SETS, judge, load_case, offline_metrics  # noqa: E402
from eval_pipeline import NO_GROUNDING  # noqa: E402
from negopilot_agent import timing  # noqa: E402
from negopilot_agent.generation import Answer  # noqa: E402
from negopilot_shared import DecompositionResult, RouteResult  # noqa: E402

DATASET_PREFIX = "negopilot-generation"


def dataset_name(set_name: str) -> str:
    return f"{DATASET_PREFIX}-{set_name}"


# ---------- 데이터셋 ----------

def build_examples(set_name: str) -> list[dict[str, Any]]:
    """정답 파일 → LangSmith 예제. 질문만 입력으로 두고, 정답 분해의 intent는 기대 출력에 넣는다."""
    gold, input_dir = SETS[set_name]
    examples = []
    for item in json.loads(gold.read_text(encoding="utf-8"))["items"]:
        query, expected_decomposition, _ = load_case(item["fixture"], input_dir)
        outputs = {"kind": item["kind"], "points": item["points"], "must_evidence": item["must_evidence"],
                   "expected_intents": [sq.intent for sq in expected_decomposition.sub_queries]}
        examples.append({"inputs": {"query": query}, "outputs": outputs,
                         "metadata": {"fixture": item["fixture"], "set": set_name,
                                      "content_sha": content_sha({"query": query} | outputs)}})
    return examples


def content_sha(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def sync_dataset(client, name: str, examples: list[dict[str, Any]], description: str) -> dict[str, list[str]]:
    """fixture 기준으로 예제를 맞춘다. 결과: {created, updated, unchanged, remote_only} fixture 목록."""
    if client.has_dataset(dataset_name=name):
        dataset = client.read_dataset(dataset_name=name)
    else:
        dataset = client.create_dataset(name, description=description)
    remote = {(e.metadata or {}).get("fixture"): e for e in client.list_examples(dataset_id=dataset.id)}
    result: dict[str, list[str]] = {"created": [], "updated": [], "unchanged": [], "remote_only": []}
    new = []
    for example in examples:
        fixture = example["metadata"]["fixture"]
        existing = remote.get(fixture)
        if existing is None:
            new.append(example)
            result["created"].append(fixture)
        elif (existing.metadata or {}).get("content_sha") != example["metadata"]["content_sha"]:
            client.update_example(existing.id, inputs=example["inputs"], outputs=example["outputs"],
                                  metadata=example["metadata"], dataset_id=dataset.id)
            result["updated"].append(fixture)
        else:
            result["unchanged"].append(fixture)
    if new:
        client.create_examples(dataset_id=dataset.id, examples=new)
    local = {e["metadata"]["fixture"] for e in examples}
    result["remote_only"] = sorted(f for f in remote if f not in local)
    return result


# ---------- 실험 대상 ----------

def make_target(graph):
    """질문 하나로 실제 그래프를 실행한다. 채점은 하지 않는다 (평가자가 따로 한다)."""
    async def target(inputs: dict) -> dict:
        with timing.collect() as measured:
            state = await graph.ainvoke({"query": inputs["query"]})
        answer: Answer = state["answer"]
        routing = state.get("routing") or RouteResult(status="not_routed")
        return {
            "answer": answer.model_dump(mode="json"),
            "decomposition": state["decomposition"].model_dump(mode="json", by_alias=True),
            "routing": routing.model_dump(mode="json"),
            "timings": measured.summary(),
        }
    return target


# ---------- 평가자 ----------
# LangSmith는 인자 이름(outputs, reference_outputs)으로 값을 넘긴다. 대상이 실패하면 outputs가 비어 있다.

def _parsed(outputs: dict | None) -> tuple[Answer, DecompositionResult, RouteResult] | None:
    if not outputs or "answer" not in outputs:
        return None
    return (Answer.model_validate(outputs["answer"]), DecompositionResult.model_validate(outputs["decomposition"]),
            RouteResult.model_validate(outputs["routing"]))


def agent_checks(outputs: dict | None, reference_outputs: dict) -> dict:
    """모델 호출 없이 계산하는 지표. eval_pipeline.evaluate_case와 같은 기준이다."""
    parsed = _parsed(outputs)
    if parsed is None:
        return {"results": [{"key": "execution_error", "score": 1}, {"key": "kind_ok", "score": 0}]}
    answer, decomposition, routing = parsed
    intents = [sq.intent for sq in decomposition.sub_queries]
    verification = answer.verification
    evidence = offline_metrics({"must_evidence": reference_outputs["must_evidence"]}, decomposition, routing)
    results = [
        {"key": "execution_error", "score": 0},
        {"key": "kind_ok", "score": int(answer.kind == reference_outputs["kind"]),
         "comment": f"{answer.kind} (정답 {reference_outputs['kind']})"},
        {"key": "intent_match", "score": int(intents == reference_outputs["expected_intents"]),
         "comment": f"{intents} (정답 {reference_outputs['expected_intents']})"},
        {"key": "retried", "score": int(bool(verification and verification.attempts > 1))},
        {"key": "masked", "score": int(bool(verification and verification.masked))},
        {"key": "tool_errors", "score": sum(call.error is not None for outcome in routing.outcomes
                                            for call in [*outcome.calls, *outcome.referenced_articles])},
    ]
    if answer.kind == "answer" and not set(intents) <= NO_GROUNDING:
        results.append({"key": "grounded_when_needed", "score": int(answer.grounded)})
    if evidence["must_evidence"]:
        results.append({"key": "must_evidence", "score": evidence["must_found"] / evidence["must_evidence"],
                        "comment": "빠진 근거: " + ", ".join(evidence["missing_evidence"]) if evidence["missing_evidence"] else None})
    return {"results": results}


def make_judge_evaluator(client, model: str):
    """정답 포인트를 LLM으로 채점한다 (eval_generation.judge 그대로). 포인트가 없는 문항은 채점하지 않는다."""
    async def point_coverage(outputs: dict | None, reference_outputs: dict) -> dict:
        points = reference_outputs["points"]
        parsed = _parsed(outputs)
        if not points:
            return {"results": []}
        if parsed is None or parsed[0].kind != "answer":   # 답하지 못했으면 포인트 0 (eval_pipeline과 같다)
            return {"results": [{"key": "point_coverage", "score": 0}, {"key": "points_covered", "score": 0},
                                {"key": "points_total", "score": len(points)}]}
        # 평가자는 대상 실행이 끝난 뒤 따로 돌아서 채점 토큰·시간이 실험의 Agent 측정에 섞이지 않는다
        marks = await judge(client, model, {"points": points}, parsed[0])
        uncovered = marks["uncovered_points"]
        return {"results": [
            {"key": "point_coverage", "score": marks["points_covered"] / len(points),
             "comment": "빠진 포인트: " + " / ".join(uncovered) if uncovered else None},
            {"key": "points_covered", "score": marks["points_covered"]},
            {"key": "points_total", "score": len(points)},
            {"key": "unsupported_claims", "score": len(marks["unsupported_claims"]),
             "comment": " / ".join(marks["unsupported_claims"]) or None},
        ]}
    return point_coverage


def summarize(rows: list[dict]) -> dict:
    """실험 결과 → eval_pipeline 보고서와 같은 방식의 합계. 정답 포인트 커버율은 포인트 수로 가중한다."""
    scores: dict[str, list[float]] = {}
    for row in rows:
        for result in row["evaluation_results"]["results"]:
            if result.score is not None:
                scores.setdefault(result.key, []).append(float(result.score))

    def mean(key):
        values = scores.get(key)
        return sum(values) / len(values) if values else None

    covered, total = sum(scores.get("points_covered", [])), sum(scores.get("points_total", []))
    return {"cases": len(rows), "execution_errors": int(sum(scores.get("execution_error", []))),
            "kind_accuracy": mean("kind_ok"), "intent_match": mean("intent_match"),
            "grounded_when_needed": mean("grounded_when_needed"), "must_evidence": mean("must_evidence"),
            "retried": int(sum(scores.get("retried", []))), "masked": int(sum(scores.get("masked", []))),
            "tool_errors": int(sum(scores.get("tool_errors", []))),
            "point_coverage": covered / total if total else None,
            "unsupported_claims": int(sum(scores.get("unsupported_claims", [])))}


# ---------- 실행 ----------

def git_revision() -> str:
    revision = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return revision + ("-dirty" if dirty else "")


def upload(args, client) -> int:
    name = args.dataset or dataset_name(args.set)
    gold, _ = SETS[args.set]
    result = sync_dataset(client, name, build_examples(args.set),
                          description=f"NegoPilot Generation {args.set} 세트 ({gold.relative_to(ROOT)}). "
                                      "검증 세트 오답을 보고 프롬프트를 고치지 않는다.")
    print(f"{name}: 새로 {len(result['created'])}개, 고침 {len(result['updated'])}개, 그대로 {len(result['unchanged'])}개")
    if result["remote_only"]:
        print(f"경고: 로컬 정답 파일에 없는 예제 {result['remote_only']} (지우지 않았다)")
    return 0


async def run(args, client) -> int:
    from langsmith import aevaluate

    from negopilot_agent.decomposition import DecomposerSettings
    from negopilot_agent.generation import GeneratorSettings
    from negopilot_agent.graph import build_graph
    import openai

    name = args.dataset or dataset_name(args.set)
    if not client.has_dataset(dataset_name=name):
        print(f"데이터셋 {name}이 없다. 먼저 upload를 실행한다.")
        return 1
    d, g = DecomposerSettings(), GeneratorSettings()
    revision = git_revision()
    judge_client = None if args.no_judge else openai.AsyncOpenAI()
    evaluators = [agent_checks] + ([] if judge_client is None else [make_judge_evaluator(judge_client, args.judge_model)])
    data = client.list_examples(dataset_name=name, limit=args.limit) if args.limit else name
    print(f"{name} × {args.repeat}회, 실험 '{args.prefix or args.set}-{revision}'. 모델·채점 API 비용이 발생합니다.", flush=True)
    try:
        results = await aevaluate(
            make_target(build_graph()), data=data, evaluators=evaluators, client=client,
            experiment_prefix=f"{args.prefix or args.set}-{revision}", num_repetitions=args.repeat,
            max_concurrency=args.concurrency,
            description=args.description,
            metadata={"revision": revision, "set": args.set,
                      "decomposer": {"backend": d.backend, "model": d.model},
                      "generator": {"backend": g.backend, "model": g.model},
                      "judge_model": None if judge_client is None else args.judge_model})
        rows = [row async for row in results]
    finally:
        if judge_client is not None:
            await judge_client.close()
    print(json.dumps(summarize(rows), ensure_ascii=False, indent=2))
    print(f"실험: {results.experiment_name}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("upload", "run"):
        p = sub.add_parser(command)
        p.add_argument("--set", choices=SETS, default="validation")
        p.add_argument("--dataset", help=f"데이터셋 이름 (기본 {DATASET_PREFIX}-<세트>)")
    run_parser = sub.choices["run"]
    run_parser.add_argument("--limit", type=int, help="앞에서부터 n개 예제만")
    run_parser.add_argument("--repeat", type=int, default=1, help="예제마다 반복 실행 수 (답변 흔들림 확인)")
    run_parser.add_argument("--concurrency", type=int, default=1, help="동시에 실행할 예제 수. 로컬 MCP는 1~2 권장")
    run_parser.add_argument("--no-judge", action="store_true", help="LLM 채점 없이 종류·intent·근거·재시도 지표만")
    run_parser.add_argument("--judge-model", default="gpt-4.1-mini")
    run_parser.add_argument("--prefix", help="실험 이름 앞부분 (기본: 세트 이름). 뒤에 커밋이 붙는다")
    run_parser.add_argument("--description", help="실험 설명 (예: 'mask 버그 수정 후')")
    args = parser.parse_args()
    if args.command == "run" and (args.repeat < 1 or args.concurrency < 1 or (args.limit is not None and args.limit < 1)):
        parser.error("--repeat, --concurrency, --limit는 1 이상이어야 합니다")

    load_env()
    if not os.getenv("LANGSMITH_API_KEY", "").strip():
        parser.error("agent/.env에 LANGSMITH_API_KEY가 필요합니다")
    if args.command == "run":
        # 예제마다 노드·LLM·MCP 호출이 실험 아래에 남도록 이 실행 동안만 추적을 켠다 (tracing.enabled 참고)
        os.environ["LANGSMITH_TRACING"] = "true"
    from langsmith import Client

    client = Client()
    return upload(args, client) if args.command == "upload" else asyncio.run(run(args, client))


if __name__ == "__main__":
    sys.exit(main())
