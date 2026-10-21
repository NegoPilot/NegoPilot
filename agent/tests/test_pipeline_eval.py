"""전체 평가의 실패 집계와 채점 비용 분리를 API 비용 없이 검사한다."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from eval_pipeline import evaluate_case, summarize
from eval_generation import SETS
from negopilot_agent import timing
from tests.test_graph import make_app


def case(name):
    gold, directory = SETS["dev"]
    return next(x for x in json.loads(gold.read_text())["items"] if x["fixture"] == name), directory


class JudgeClient:
    def __init__(self, error=False, malformed=False):
        self.chat = SimpleNamespace(completions=self)
        self.error, self.malformed = error, malformed

    async def create(self, **kwargs):
        if self.error:
            raise RuntimeError("judge unavailable")
        count = 0 if self.malformed else 3
        result = {"points": [{"index": i, "covered": True} for i in range(count)], "unsupported_claims": []}
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
                content=json.dumps(result), refusal=None))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20,
                                  prompt_tokens_details=SimpleNamespace(cached_tokens=50)))


async def test_judge_tokens_separate_from_agent_and_actual_answer_saved():
    graph, _, _, _ = make_app("01_law_article")
    item, directory = case("01_law_article")
    row = await evaluate_case(graph, item, directory, JudgeClient())
    assert row["kind_ok"] and row["judge_status"] == "ok"
    assert row["answer"]["text"] and row["routing"]["status"] == "routed"
    assert row["timings"]["input_tokens"] == 0
    assert row["judge_timings"]["input_tokens"] == 100
    assert row["judge_timings"]["cached_tokens"] == 50
    assert summarize([row])["covered_points"] == 3


@pytest.mark.parametrize("name", ["09_clarify", "10_out_of_scope", "11_failed"])
async def test_skipped_routing_is_not_an_execution_error(name):
    graph, _, _, _ = make_app(name)
    item, directory = case(name)
    row = await evaluate_case(graph, item, directory)
    assert "error" not in row and row["kind_ok"]


@pytest.mark.parametrize("malformed", [False, True])
async def test_judge_failure_preserves_agent_result(malformed):
    graph, _, _, _ = make_app("01_law_article")
    item, directory = case("01_law_article")
    row = await evaluate_case(graph, item, directory, JudgeClient(error=not malformed, malformed=malformed))
    assert row["judge_status"] == "error" and row["answer"]["text"]
    summary = summarize([row])
    assert summary["judge_errors"] == 1 and summary["scored_points"] == 0
    assert summary["point_coverage"] is None
    assert summary["point_coverage_lower_bound"] == 0


async def test_failed_graph_preserves_spent_tokens_and_failure_denominator():
    class BrokenGraph:
        async def ainvoke(self, state):
            with timing.span("llm", "fake") as meta:
                meta.update(input_tokens=90, output_tokens=5)
            raise RuntimeError("tool failure")
    item, directory = case("01_law_article")
    row = await evaluate_case(BrokenGraph(), item, directory)
    summary = summarize([row])
    assert summary["execution_errors"] == 1 and summary["kind_accuracy"] == 0
    assert summary["agent"]["input_tokens"]["mean"] == 90
    assert summary["expected_points"] == 3


async def test_wrong_answer_kind_is_scored_as_zero_points():
    graph, _, _, _ = make_app("09_clarify")
    item, directory = case("09_clarify")
    item = {**item, "kind": "answer", "points": ["필수 정답"]}
    row = await evaluate_case(graph, item, directory)
    assert row["judge_status"] == "wrong_kind" and row["points_covered"] == 0
    assert not row["kind_ok"]


async def test_no_judge_does_not_report_unmeasured_quality_as_zero_or_success():
    graph, _, _, _ = make_app("01_law_article")
    item, directory = case("01_law_article")
    row = await evaluate_case(graph, item, directory)
    assert row["judge_status"] == "disabled"
    summary = summarize([row])
    assert summary["point_coverage"] is None and summary["scored_points"] == 0
    assert summary["judge"] == {}


async def test_intent_match_and_grounding_only_count_answers_that_need_evidence():
    """분해 intent 일치와, 근거가 필요한 답변만 센 근거 표시율 (#110). 개념 설명은 근거 표시율에서 빠진다."""
    rows = []
    for name in ("01_law_article", "06_general_knowledge"):
        graph, _, _, _ = make_app(name)
        item, directory = case(name)
        rows.append(await evaluate_case(graph, item, directory))
    law, concept = rows
    assert law["intent_match"] and concept["intent_match"]
    assert law["needs_grounding"] and not concept["needs_grounding"]
    summary = summarize(rows)
    assert summary["intent_match"] == 1.0
    assert summary["grounded_when_needed"] == (1.0 if law["grounded"] else 0.0)


async def test_cross_judge_scores_the_same_answer_twice():
    graph, _, _, _ = make_app("01_law_article")
    item, directory = case("01_law_article")
    row = await evaluate_case(graph, item, directory, JudgeClient(), cross_model="other-model")
    assert row["cross"]["points_covered"] == 3
    assert summarize([row])["cross_judge"]["point_agreement"] == 1.0


def test_cross_metrics_counts_point_level_disagreement():
    from eval_pipeline import cross_metrics
    rows = [{"points": 3, "points_covered": 2, "covered_points": [0, 1], "unsupported_claims": [],
             "cross": {"points_covered": 1, "covered_points": [0], "unsupported_claims": ["a", "b"]}}]
    m = cross_metrics(rows)
    assert m["point_agreement"] == 2 / 3
    assert (m["unsupported_claims"], m["cross_unsupported_claims"]) == (0, 2)
    assert cross_metrics([{"points": 1}]) is None
