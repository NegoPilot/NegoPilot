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


def scored(case, run, covered, points=4, kind_ok=True, intent_match=True):
    """채점까지 끝난 결과 한 줄 (모델 호출 없음)."""
    return {"case": case, "run": run, "points": points, "points_covered": covered, "kind_ok": kind_ok,
            "kind": "answer" if kind_ok else "clarify", "intent_match": intent_match, "timings": {"total": 1.0}}


def test_spread_reports_per_run_values_and_standard_error():
    """회차별 값과 흩어짐 (#218). 회차가 1개면 흩어짐을 내지 않는다."""
    from eval_pipeline import spread
    rows = [scored("a", 1, 4), scored("b", 1, 2), scored("a", 2, 4), scored("b", 2, 4)]
    s = spread(rows)["point_coverage"]
    assert s["per_run"] == [0.75, 1.0]
    assert abs(s["stdev"] - 0.1768) < 1e-3 and abs(s["sem"] - 0.125) < 1e-3
    assert spread(rows[:2]) == {}


def test_spread_skips_runs_that_were_not_fully_judged():
    from eval_pipeline import spread
    unjudged = {key: value for key, value in scored("a", 2, 0).items() if key != "points_covered"}
    rows = [scored("a", 1, 4), unjudged, scored("a", 3, 2)]
    assert spread(rows)["point_coverage"]["per_run"] == [1.0, 0.5]


def test_by_case_shows_which_case_fails_every_run():
    from eval_pipeline import by_case
    rows = [scored("a", 1, 4), scored("b", 1, 0, kind_ok=False), scored("a", 2, 3), scored("b", 2, 0, kind_ok=False)]
    cases = by_case(rows)
    assert (cases["a"]["kind_ok"], cases["a"]["covered"], cases["a"]["points"]) == (2, 7, 8)
    assert (cases["b"]["kind_ok"], cases["b"]["runs"]) == (0, 2)


def test_compare_marks_small_differences_as_noise_and_large_ones_as_change():
    from eval_pipeline import compare
    base_rows = [scored("a", 1, 3), scored("a", 2, 4), scored("a", 3, 3)]
    same = summarize([scored("a", 1, 4), scored("a", 2, 3), scored("a", 3, 3)])
    base = {"summary": summarize(base_rows)}
    assert compare(base, same)["point_coverage"]["verdict"] == "흔들림 범위"
    worse = summarize([scored("a", 1, 1), scored("a", 2, 1), scored("a", 3, 2)])
    assert compare(base, worse)["point_coverage"]["verdict"] == "하락"
    # 지금 결과가 한 회차뿐이면 흔들림 폭을 모른다
    assert compare(base, summarize([scored("a", 1, 4)]))["point_coverage"]["verdict"] == "흔들림 모름"


def test_report_includes_spread_case_table_and_comparison_warning():
    from eval_pipeline import report
    rows = [scored("a", 1, 3), scored("a", 2, 4)]
    metadata = {"set": "dev", "revision": "abcdef123", "dirty": False, "repeat": 2,
                "decomposer": {"model": "m"}, "generator": {"model": "m"}, "judge_model": "j"}
    base = {"set": "validation", "revision": "1234567", "decomposer": {"model": "m"},
            "generator": {"model": "m"}, "summary": summarize(rows)}
    text = report(metadata, summarize(rows), rows, base, "eval_results/pipeline-old.json")
    assert "정답 포인트 커버 회차별 | 75.0%, 100.0%" in text
    assert "| a | 2/2 | 2/2 | 7/8 |" in text
    assert "같은 기준 비교가 아니다" in text
