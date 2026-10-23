"""eval_e2e.py 요약 (#110). 모델·MCP 호출 없음."""

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("eval_e2e", Path(__file__).resolve().parents[1] / "scripts/eval_e2e.py")
eval_e2e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(eval_e2e)


def row(fixture, intents, grounded, covered=None, points=2, kind="answer"):
    r = {"fixture": fixture, "kind": kind, "kind_ok": True, "intents": intents, "expected_intents": intents,
         "grounded": grounded, "points": points, "seconds": 1.0, "cost_usd": 0.001}
    if covered is not None:
        r |= {"points_covered": covered, "covered_points": list(range(covered)), "unsupported_claims": []}
    return r


def test_grounded_counts_only_answers_that_need_evidence():
    rows = [row("law", ["law_check"], True, covered=2),
            row("concept", ["general_knowledge"], False),          # 근거 없이 답하는 게 맞음 → 세지 않음
            {"fixture": "broken", "points": 1, "kind_ok": False, "error": "TimeoutError", "seconds": 9.0}]
    text = eval_e2e.summarize(rows)
    assert "| 근거 표시 있음 (근거가 필요한 답변) | 100% (1/1) |" in text
    assert "| 실행 오류 | 33% (1/3) |" in text
    assert "| 정답 포인트 커버 | 100% (2/2) |" in text
