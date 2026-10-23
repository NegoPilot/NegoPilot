"""eval_generation.py 교차 채점 요약 (#106). 모델 호출 없음."""

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("eval_generation", Path(__file__).resolve().parents[1] / "scripts/eval_generation.py")
eval_generation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(eval_generation)


def test_cross_summary_counts_point_level_agreement():
    rows = [
        {"points": 3, "covered_points": [0, 1], "cross": {"points_covered": 1, "covered_points": [0], "unsupported_claims": ["a"]}},
        {"points": 2, "covered_points": [0, 1], "cross": {"points_covered": 2, "covered_points": [0, 1], "unsupported_claims": []}},
    ]
    lines = eval_generation.cross_summary(rows)
    assert "60% (3/5)" in lines[0]          # 교차 채점 커버: 1 + 2
    assert "0.50개" in lines[1]
    assert "80% (4/5)" in lines[2]          # 판정이 갈린 것은 첫 답변의 포인트 1 하나
