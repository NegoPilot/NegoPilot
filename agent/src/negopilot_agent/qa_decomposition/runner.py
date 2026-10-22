"""Offline QA comparison runner. Reports are wiring checks, never model accuracy."""

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Any, Callable, Literal
from uuid import uuid4

from langsmith import tracing_context
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from negopilot_agent.api.models import HistoryEntry, NonBlankText
from negopilot_agent.api.supplier_chat_models import Context

from .executor import execute
from .fixtures import FixtureToolCaller
from .loader import ModuleConfigurationError, select_decomposer

QA_BRANCH = "qa/decomposer-swap"
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
RESULTS_DIRECTORY = Path("agent/qa/decomposer/results")


class ExpectedCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["internal", "mcp", "binding"]
    target: str
    arguments: dict
    status: Literal["ok", "error"] = "ok"


class Expectation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["completed", "clarify", "validation_failed", "execution_failed", "failed"]
    classification: Literal["internal", "mcp", "mcp_then_internal", "none", "mixed"]
    calls: list[ExpectedCall]
    result_subset: Any = None
    error_code: str | None = None


class QACase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    query: NonBlankText
    history: list[HistoryEntry] = Field(default_factory=list, max_length=20)
    context: Context | None = None
    expected: Expectation

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


def load_cases(path: Path) -> list[QACase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("Cases must be a nonempty JSON array")
    cases = [QACase.model_validate(item) for item in raw]
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("Case IDs must be unique")
    return cases


def _snapshot(value: Any) -> Any:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    # Reject non-JSON objects rather than silently losing malformed output through repr().
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _subset(expected: Any, actual: Any, path: str, failures: list[dict]) -> None:
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key, value in expected.items():
            if key not in actual:
                failures.append({"path": f"{path}.{key}", "reason": "missing_key", "expected": value})
            else:
                _subset(value, actual[key], f"{path}.{key}", failures)
    elif isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        for index, (left, right) in enumerate(zip(expected, actual)):
            _subset(left, right, f"{path}[{index}]", failures)
    elif expected != actual or type(expected) is not type(actual):
        failures.append({"path": path, "reason": "value_mismatch", "expected": expected, "actual": actual})


def compare(expected: Expectation, execution: dict) -> dict:
    failures: list[dict] = []
    for key in ("status", "classification"):
        _subset(getattr(expected, key), execution.get(key), key, failures)
    actual_calls = [{key: event.get(key) for key in ("kind", "target", "arguments", "status")}
                    for event in execution.get("events", [])]
    # The full call list is ordered and exact. Extra calls and extra arguments fail.
    expected_calls = [call.model_dump() for call in expected.calls]
    if expected_calls != actual_calls:
        failures.append({"path": "calls", "reason": "ordered_calls_mismatch",
                         "expected": expected_calls, "actual": actual_calls})
    if "result_subset" in expected.model_fields_set:
        _subset(expected.result_subset, execution.get("final_result"), "final_result", failures)
    if expected.error_code is not None:
        codes = [error.get("code") for error in execution.get("errors", [])]
        if expected.error_code not in codes:
            failures.append({"path": "errors", "reason": "missing_error_code",
                             "expected": expected.error_code, "actual": codes})
    return {"matched": not failures, "failures": failures}


def _failed_execution(code: str, error: Exception) -> dict:
    return {"status": "failed", "classification": "none", "normalized_plan": None,
            "events": [], "step_results": {}, "final_result": None, "clarification": None,
            "errors": [{"code": code, "message": "QA case could not complete",
                        "exception_type": type(error).__name__}]}


async def run_cases(cases: list[QACase], *, decomposer: Any, metadata: dict,
                    caller_factory: Callable = FixtureToolCaller) -> dict:
    records = []
    for case in cases:
        payload = case.model_dump(mode="json", exclude={"expected", "id"})
        raw_output = None
        raw_output_encoding = "json"
        stage = "DECOMPOSER_ERROR"
        try:
            # Pass copies so a candidate cannot mutate the retained test input or expectation.
            with tracing_context(enabled=False):
                raw = await decomposer.decompose(payload["query"], deepcopy(payload["history"]),
                                                 context=deepcopy(payload["context"]))
                stage = "OUTPUT_SERIALIZATION_ERROR"
                raw_output = _snapshot(raw)
                stage = "EXECUTOR_ERROR"
                execution = await execute(raw, query=payload["query"], caller=caller_factory())
        except Exception as error:
            if stage == "OUTPUT_SERIALIZATION_ERROR":
                # Retain NaN/Infinity exactly as text without placing invalid numbers in the report.
                # Unsupported Python objects are outside the decomposer's JSON contract.
                try:
                    value = raw.model_dump(mode="json", by_alias=True) if isinstance(raw, BaseModel) else raw
                    raw_output = json.dumps(value, ensure_ascii=False, allow_nan=True)
                    raw_output_encoding = "nonstandard_json_text"
                except (TypeError, ValueError, OverflowError, RecursionError):
                    raw_output = {"python_type": type(raw).__name__}
                    raw_output_encoding = "unsupported_python_type"
            execution = _failed_execution(stage, error)
        records.append({"id": case.id, "input": payload,
                        "expected": case.expected.model_dump(mode="json", exclude_unset=True),
                        "raw_output": raw_output, "raw_output_encoding": raw_output_encoding,
                        "execution": execution,
                        "comparison": compare(case.expected, execution)})
    matched = sum(record["comparison"]["matched"] for record in records)
    return {"schema_version": "qa-comparison.v1", "purpose": "wiring_validation",
            "classification_performance_evaluated": False,
            "notice": "The selected decomposer is compared using MCP test doubles. Wiring checks do not establish model classification accuracy.",
            "module": deepcopy(metadata), "io": "test-double", "cases": records,
            "summary": {"total": len(records), "matched": matched, "mismatched": len(records) - matched}}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def check_workspace(root: Path) -> dict:
    root = root.resolve()
    if not (root / ".git").is_file():
        raise ValueError("QA runs require the separate linked worktree")
    branch = _git(root, "branch", "--show-current")
    if branch != QA_BRANCH:
        raise ValueError(f"QA runs are restricted to branch {QA_BRANCH}")
    return {"branch": branch, "commit": _git(root, "rev-parse", "HEAD"),
            "worktree": str(root), "dirty": bool(_git(root, "status", "--porcelain"))}


def checked_output_path(root: Path, output: str | Path | None) -> Path:
    root = root.resolve()
    allowed = root / RESULTS_DIRECTORY
    # Reject a symlink in any results ancestor as well as symlinked report files.
    if allowed.resolve() != allowed:
        raise ValueError("The QA results directory must not contain symlink redirects")
    if output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = allowed / f"wiring-{stamp}-{uuid4().hex[:8]}.json"
    else:
        target = Path(output).absolute()
    resolved = target.resolve()
    if resolved != target or not resolved.is_relative_to(allowed) or resolved == allowed:
        raise ValueError("Reports must stay inside this QA worktree's agent/qa/decomposer/results directory")
    if resolved.suffix != ".json":
        raise ValueError("QA reports must use a .json suffix")
    if resolved.exists():
        raise ValueError("A report already exists at this path; choose a new report name")
    return resolved


def write_report(report: dict, target: Path) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        stream.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Isolated QueryDecomposer wiring checks. No model accuracy evaluation.")
    parser.add_argument("--module", choices=("existing", "qa"))
    parser.add_argument("--factory", help="Trusted Python module:factory for a QA replacement")
    parser.add_argument("--allow-model-calls", action="store_true",
                        help="Explicit future opt-in for a real decomposer; not used by wiring verification")
    parser.add_argument("--io", choices=("test-double",), default="test-double")
    parser.add_argument("--cases", type=Path, default=REPOSITORY_ROOT / "agent/qa/decomposer/cases.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        provenance = check_workspace(REPOSITORY_ROOT)
        target = checked_output_path(REPOSITORY_ROOT, args.output)
        cases = load_cases(args.cases)
        selected = select_decomposer(args.module, factory=args.factory, allow_model_calls=args.allow_model_calls)
        report = asyncio.run(run_cases(cases, decomposer=selected.decomposer, metadata=selected.metadata))
        report["provenance"] = provenance
        report["created_at"] = datetime.now(timezone.utc).isoformat()
        write_report(report, target)
    except (ValueError, OSError, subprocess.CalledProcessError, ValidationError, ModuleConfigurationError) as error:
        # Configuration errors are safe fixed messages. Pydantic values and process stderr can contain user data.
        if isinstance(error, ValidationError):
            message = "Case file failed schema validation"
        elif isinstance(error, (OSError, subprocess.CalledProcessError)):
            message = f"QA setup or report writing failed ({type(error).__name__})"
        else:
            message = str(error)
        print(f"QA setup rejected: {message}")
        return 2
    summary = report["summary"]
    print(f"Wiring checks: {summary['matched']}/{summary['total']} matched. Model classification performance was not evaluated.")
    print(f"Report: {target}")
    return 0 if summary["mismatched"] == 0 else 1
