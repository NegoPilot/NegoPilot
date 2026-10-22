"""LangSmith 평가 스크립트의 데이터셋 구성·동기화와 평가자를 네트워크 없이 검사한다."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langsmith.evaluation import EvaluationResult

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from eval_generation import SETS
from eval_langsmith import (agent_checks, build_examples, make_judge_evaluator, make_target, summarize,
                            sync_dataset)
from tests.test_graph import make_app
from tests.test_pipeline_eval import JudgeClient


def scores(result: dict) -> dict:
    return {r["key"]: r["score"] for r in result["results"]}


def reference(set_name: str, fixture: str) -> dict:
    return next(e for e in build_examples(set_name) if e["metadata"]["fixture"] == fixture)["outputs"]


@pytest.mark.parametrize("set_name", list(SETS))
def test_examples_mirror_gold_items_with_question_only_inputs(set_name):
    gold, _ = SETS[set_name]
    items = json.loads(gold.read_text(encoding="utf-8"))["items"]
    examples = build_examples(set_name)
    assert [e["metadata"]["fixture"] for e in examples] == [i["fixture"] for i in items]
    for example, item in zip(examples, items):
        assert set(example["inputs"]) == {"query"} and example["inputs"]["query"]
        assert example["outputs"]["kind"] == item["kind"] and example["outputs"]["points"] == item["points"]
        assert isinstance(example["outputs"]["expected_intents"], list)
    assert build_examples(set_name) == examples   # 같은 파일이면 content_sha도 같다


class FakeLangSmith:
    """has_dataset·read_dataset·create_dataset·list_examples·create_examples·update_example만 흉내 낸다."""

    def __init__(self):
        self.datasets: dict[str, SimpleNamespace] = {}
        self.examples: dict = {}
        self.updated: list = []

    def has_dataset(self, *, dataset_name):
        return dataset_name in self.datasets

    def read_dataset(self, *, dataset_name):
        return self.datasets[dataset_name]

    def create_dataset(self, name, *, description):
        self.datasets[name] = SimpleNamespace(id=uuid4(), name=name, description=description)
        return self.datasets[name]

    def list_examples(self, *, dataset_id):
        return [e for e in self.examples.values() if e.dataset_id == dataset_id]

    def create_examples(self, *, dataset_id, examples):
        for example in examples:
            eid = uuid4()
            self.examples[eid] = SimpleNamespace(id=eid, dataset_id=dataset_id, **example)

    def update_example(self, example_id, *, inputs, outputs, metadata, dataset_id):
        self.updated.append(example_id)
        self.examples[example_id] = SimpleNamespace(id=example_id, dataset_id=dataset_id, inputs=inputs,
                                                    outputs=outputs, metadata=metadata)


def test_sync_creates_then_is_idempotent_then_updates_changed_examples():
    client, examples = FakeLangSmith(), build_examples("validation")
    first = sync_dataset(client, "ds", examples, "설명")
    assert len(first["created"]) == len(examples) and not first["updated"]
    second = sync_dataset(client, "ds", examples, "설명")
    assert len(second["unchanged"]) == len(examples) and not second["created"] and not client.updated

    changed = json.loads(json.dumps(examples))
    changed[0]["outputs"]["points"] = changed[0]["outputs"]["points"] + ["새 포인트"]
    changed[0]["metadata"]["content_sha"] = "changed"
    third = sync_dataset(client, "ds", changed, "설명")
    assert third["updated"] == [examples[0]["metadata"]["fixture"]]
    assert len(client.examples) == len(examples)   # 고친 예제는 새로 만들지 않는다


def test_sync_reports_but_keeps_examples_missing_locally():
    client, examples = FakeLangSmith(), build_examples("validation")
    sync_dataset(client, "ds", examples, "설명")
    result = sync_dataset(client, "ds", examples[1:], "설명")
    assert result["remote_only"] == [examples[0]["metadata"]["fixture"]]
    assert len(client.examples) == len(examples)


async def test_target_and_agent_checks_match_pipeline_criteria():
    graph, query, _, _ = make_app("01_law_article")
    outputs = await make_target(graph)({"query": query})
    assert outputs["answer"]["text"] and outputs["routing"]["status"] == "routed"
    assert "total" in outputs["timings"]
    result = scores(agent_checks(outputs, reference("dev", "01_law_article")))
    assert result["execution_error"] == 0 and result["kind_ok"] == 1 and result["intent_match"] == 1
    assert result["tool_errors"] == 0 and result["masked"] == 0
    assert result["grounded_when_needed"] == 1


@pytest.mark.parametrize("name", ["09_clarify", "10_out_of_scope"])
async def test_answers_without_grounding_are_not_scored_for_grounding(name):
    graph, query, _, _ = make_app(name)
    outputs = await make_target(graph)({"query": query})
    result = scores(agent_checks(outputs, reference("dev", name)))
    assert result["kind_ok"] == 1 and "grounded_when_needed" not in result


@pytest.mark.parametrize("outputs", [None, {}])
def test_failed_target_counts_as_execution_error(outputs):
    result = scores(agent_checks(outputs, reference("dev", "01_law_article")))
    assert result == {"execution_error": 1, "kind_ok": 0}


async def test_judge_evaluator_scores_points_and_skips_cases_without_points():
    graph, query, _, _ = make_app("01_law_article")
    outputs = await make_target(graph)({"query": query})
    ref = reference("dev", "01_law_article")
    evaluator = make_judge_evaluator(JudgeClient(), "fake-judge")
    result = scores(await evaluator(outputs, ref | {"points": ref["points"][:3]}))
    assert result["point_coverage"] == 1 and result["points_covered"] == 3 and result["points_total"] == 3
    assert (await evaluator(outputs, ref | {"points": []})) == {"results": []}


async def test_judge_evaluator_gives_zero_when_the_agent_did_not_answer():
    evaluator = make_judge_evaluator(JudgeClient(error=True), "fake-judge")   # 채점 API를 부르면 실패한다
    result = scores(await evaluator({}, {"points": ["a", "b"]}))
    assert result == {"point_coverage": 0, "points_covered": 0, "points_total": 2}


def test_summary_weights_point_coverage_by_points():
    def row(*pairs):
        return {"evaluation_results": {"results": [EvaluationResult(key=k, score=s) for k, s in pairs]}}
    rows = [row(("execution_error", 0), ("kind_ok", 1), ("points_covered", 1), ("points_total", 1)),
            row(("execution_error", 0), ("kind_ok", 0), ("points_covered", 1), ("points_total", 3)),
            row(("execution_error", 1), ("kind_ok", 0))]
    summary = summarize(rows)
    assert summary["cases"] == 3 and summary["execution_errors"] == 1
    assert summary["kind_accuracy"] == pytest.approx(1 / 3)
    assert summary["point_coverage"] == pytest.approx(2 / 4)   # 문항 평균(0.67)이 아니라 포인트 가중
