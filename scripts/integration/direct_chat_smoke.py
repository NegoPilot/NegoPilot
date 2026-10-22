#!/usr/bin/env python3
"""Exercise documented direct-chat stub questions through real Python and Spring.

The production stub API runs the real graph and AnswerGenerator. A separate
caller-only failure server checks tool errors. Spring runs its default profile
against a disposable PostgreSQL container with a signed-in member. No model or
dependency install is used. Reports and logs stay under /tmp and only owned
processes and containers are stopped.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Any
import uuid

from generation_smoke import (
    CONTRACT, CONVERSATIONS, INTERNAL, PUBLIC, ROOT, GenerationRelay, GenerationSmoke,
    Process, child_environment, context, request, require, require_tmp_output, unused_port,
)

DEFAULT_REPORT = Path("/tmp/negopilot-direct-chat-smoke-results.json")
TOOL_DIAGNOSTIC = "private-direct-chat-tool-diagnostic"
FINANCIAL_QUERY = "이 회사의 재무 상태를 알려줘"
AMBIGUOUS_QUERY = "해솔에 대해 알려줘"
OPTIONS_SEPARATOR = "\n\n선택지 데이터:\n"
LAW_QUERIES = ["하도급법상 대금 지급 기한은 며칠이야?", "결제 90일로 해도 돼?"]
SEARCH_CASES = [
    ("협력사 목록 보여줘", {}, {"SUP-0034", "SUP-0041", "SUP-0052", "SUP-0060"}),
    ("부산 협력사 찾아줘", {"region": "부산"}, {"SUP-0041"}),
    ("경남 협력사 찾아줘", {"region": "경남"}, {"SUP-0060"}),
    ("프레스 협력사 찾아줘", {"process": "프레스"}, {"SUP-0052", "SUP-0060"}),
    ("제주 협력사 찾아줘", {"region": "제주"}, set()),
]


def load_suppliers() -> dict[str, dict[str, Any]]:
    directory = ROOT / "docs/contracts/fixtures"
    rows = []
    for name in ("supplier-chat.json", "direct-chat.json"):
        rows += json.loads((directory / name).read_text(encoding="utf-8"))["suppliers"]
    require(len(rows) == len({row["supplier_id"] for row in rows}), "Duplicate fixture supplier ID")
    return {row["supplier_id"]: row for row in rows}


def calls(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [call for outcome in body["routing"]["outcomes"] for call in outcome["calls"]]


def assert_outcome(body: dict[str, Any], status: str) -> None:
    outcomes = body["routing"]["outcomes"]
    require(len(outcomes) == 1 and outcomes[0]["status"] == status, "Routing outcome status differs")


def assert_search(body: dict[str, Any], filters: dict[str, Any], expected_ids: set[str], suppliers: dict) -> None:
    assert_outcome(body, "ok" if expected_ids else "empty")
    actual_calls = calls(body)
    require(len(actual_calls) == 1 and actual_calls[0]["tool"] == "search_suppliers", "Search did not use one search_suppliers call")
    call = actual_calls[0]
    require(call["server"] == "business-mcp" and call["error"] is None, "Supplier search failed")
    require(all(call["args"].get(key) == value for key, value in filters.items()), "Search filter was lost")
    require("supplier" not in call["args"], "Search was replaced by a selected supplier")
    rows = call["result"]
    require(isinstance(rows, list) and {row["supplier_id"] for row in rows} == expected_ids, "Search fixture IDs differ")
    for row in rows:
        source = suppliers[row["supplier_id"]]
        require(all(row[key] == source[key] for key in ("name", "region", "iatf16949")), "Supplier identity differs from fixture")
        require({key: row[key] for key in ("process", "material", "monthly_capacity")} in source["capabilities"],
                "Search capability was not in the fixture")
        require(all(row.get(key) == value for key, value in filters.items()), "Search returned an unmatched row")
    require(body["answer"]["kind"] == ("answer" if expected_ids else "no_result"), "Empty search must differ from a missing choice")
    require(body["execution_status"] == "COMPLETED" and body["answer"]["options"] == [], "Search became ambiguous or failed")
    if expected_ids:
        require(bool(body["answer"]["citations"]) and bool(body["answer"]["evidence"]), "Search Answer has no verified evidence")


def assert_lookup(body: dict[str, Any], *, expected_ids: set[str], suppliers: dict,
                  expected_match_type: str | None = None) -> None:
    actual_calls = calls(body)
    require(bool(actual_calls) and actual_calls[0]["tool"] == "find_suppliers", "Name/ID lookup must use find_suppliers")
    lookup = actual_calls[0]
    require(lookup["server"] == "business-mcp" and lookup["args"].get("limit") == 10, "Lookup limit/server changed")
    require(lookup["error"] is None, "Lookup failed unexpectedly")
    result = lookup["result"]
    require(isinstance(result, dict) and set(result) == {"matches", "total", "match_type"}, "Invalid find_suppliers result")
    require(type(result["total"]) is int and result["total"] == len(expected_ids), "Lookup total differs")
    matches = result["matches"]
    require(len(matches) == len(expected_ids) and {item["supplier_id"] for item in matches} == expected_ids, "Lookup candidates differ")
    if expected_match_type is not None:
        require(result["match_type"] == expected_match_type, "Lookup match_type differs")
    for item in matches:
        require(item == {key: suppliers[item["supplier_id"]][key] for key in ("supplier_id", "name", "region")},
                "Lookup candidate differs from fixture")
    if len(expected_ids) == 1:
        supplier_id = next(iter(expected_ids))
        assert_detail(body, supplier_id, suppliers, preceded_by_lookup=True)
    elif expected_ids:
        assert_outcome(body, "missing_args")
        require(len(actual_calls) == 1, "Ambiguous lookup guessed a supplier detail")
        require(body["answer"]["kind"] == "clarify" and body["execution_status"] == "NEEDS_INPUT", "Ambiguous lookup must request a choice")
        options = body["answer"]["options"]
        require(len(options) == len(expected_ids), "Candidate option count differs")
        for supplier_id in expected_ids:
            require(sum(supplier_id in option and suppliers[supplier_id]["name"] in option for option in options) == 1,
                    "Candidate option must identify the real fixture name and ID")
    else:
        assert_outcome(body, "empty")
        require(len(actual_calls) == 1 and body["answer"]["kind"] == "no_result", "Unknown supplier was guessed or treated as ambiguity")
        require(body["execution_status"] == "COMPLETED" and body["answer"]["options"] == [], "Unknown supplier returned candidate choices")


def assert_detail(body: dict[str, Any], supplier_id: str, suppliers: dict, *, preceded_by_lookup=False) -> None:
    assert_outcome(body, "ok")
    actual_calls = calls(body)
    expected_tools = ["find_suppliers", "get_supplier"] if preceded_by_lookup else ["get_supplier"]
    require([call["tool"] for call in actual_calls] == expected_tools, "Canonical detail tool sequence differs")
    detail = actual_calls[-1]
    require(detail["server"] == "business-mcp" and detail["args"] == {"supplier": supplier_id},
            "Detail did not use the resolved canonical supplier ID")
    require(detail["error"] is None, "Canonical detail failed")
    require(all(key in detail["result"] and detail["result"][key] == value
                for key, value in suppliers[supplier_id].items()),
            "Supplier detail lost a fixture field or null")
    require(body["answer"]["kind"] == "answer" and body["execution_status"] == "COMPLETED", "Resolved detail did not answer")
    require(body["answer"]["options"] == [] and bool(body["answer"]["citations"]),
            "Resolved detail lacks citation or incorrectly asks for a choice")


def assert_resumed(body: dict[str, Any], reply: str, original: str) -> None:
    require(body["decomposition"]["original_query"] == reply, "Follow-up original_query must remain the current short reply")
    queries = body["decomposition"]["sub_queries"]
    require(len(queries) == 1 and queries[0]["intent"] == "supplier_detail", "Follow-up did not resume supplier detail")
    require(original in queries[0]["question"], "Follow-up lost the original requested task")


def assert_forwarded_history(actual: list[dict], messages: list[dict]) -> None:
    previous = messages[-20:]
    require(len(actual) == len(previous), "Forwarded history length changed")
    for entry, stored in zip(actual, previous, strict=True):
        require(set(entry) == {"role", "content"} and entry["role"] == stored["role"],
                "Forwarded history fields or role changed")
        content = stored["content"]
        if stored["role"] == "user":
            require(entry["content"] == content, "Forwarded user history changed")
            continue
        options = stored["response"]["answer"]["options"]
        limit, suffix = 4000, ""
        if options:
            _, separator, encoded = entry["content"].rpartition(OPTIONS_SEPARATOR)
            require(bool(separator), "Assistant history lost the numbered options block")
            try:
                numbered = json.loads(encoded)
            except (ValueError, TypeError):
                raise AssertionError("Assistant history options are not JSON") from None
            expected = [{"number": number, "text": option} for number, option in enumerate(options, 1)]
            require(numbered == expected and all(type(item["number"]) is int for item in numbered),
                    "History options changed their actual order, numbers or text")
            suffix = separator + encoded
            limit -= len(suffix)
            require(limit >= 1, "Options block leaves no room for assistant content")
        prefix = content[:limit]
        if len(content) > limit and not prefix.strip():
            prefix = content.lstrip()[:limit]
        require(entry["content"] == prefix + suffix and len(entry["content"]) <= 4000,
                "Assistant history text or codepoint limit changed")


def write_report(path: Path, report: dict[str, Any], *private: str) -> None:
    """Keep a detected key or password leak out of every persisted response and relay record."""
    path = require_tmp_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    for value in private:
        serialized = serialized.replace(value, "[REDACTED]")
    path.write_text(serialized + "\n", encoding="utf-8")


def tool_failure_app():
    from negopilot_agent.api.app import create_app
    from negopilot_agent.api.settings import ApiSettings

    async def failing_caller(server, tool, args):
        raise RuntimeError(TOOL_DIAGNOSTIC)

    return create_app(settings=ApiSettings.from_env(), generation_caller=failing_caller)


class DirectChatSmoke(GenerationSmoke):
    def __init__(self, jar: Path):
        super().__init__(jar)
        self.suppliers = load_suppliers()

    def start_fixture_python(self) -> int:
        return self.start_python(self.key, "Python built-in direct-chat stub")

    def forwarded(self, body, query, selected, previous):
        records = [record for record in self.relay.records if record["body"]["request_id"] == body["request_id"]]
        require(len(records) == 1 and records[0]["authenticated"], "Request forwarding/authentication mismatch")
        forwarded = records[0]["body"]
        require({key: value for key, value in forwarded.items() if key != "history"}
                == {"request_id": body["request_id"], "query": query, "context": selected},
                "Forwarded query or context changed")
        assert_forwarded_history(forwarded["history"], previous)
        public = {key: value for key, value in body.items() if key not in {"conversation_id", "response_id"}}
        require(records[0].get("upstream_response") == public, "Spring changed Answer, evidence, citations or verification")
        require(body["decomposition"]["original_query"] == query, "Decomposition original query changed")

    def query(self, query: str):
        payload = {"request_id": str(uuid.uuid4()), "query": query, "history": [], "context": context(None)}
        direct = self.accepted(self.send(self.python, INTERNAL, payload, key=self.key), selected=context(None))
        public = self.accepted(self.public(PUBLIC, payload), selected=context(None))
        require(direct == public, "Direct Python and public Spring results differ")
        self.forwarded(public, query, context(None), [])
        return public

    def search_without_selection(self):
        for query, filters, expected_ids in SEARCH_CASES:
            assert_search(self.query(query), filters, expected_ids, self.suppliers)

    def law_without_selection(self):
        recorded = json.loads((ROOT / "shared/fixtures/routing/07_number_needs_grounding.json").read_text())["routing"]
        expected = recorded["outcomes"][0]["calls"]
        for query in LAW_QUERIES:
            body = self.query(query)
            require(body["answer"]["kind"] == "answer" and body["execution_status"] == "COMPLETED", "Payment law question did not answer")
            require([item["intent"] for item in body["decomposition"]["sub_queries"]] == ["law_check"],
                    "Payment law question incorrectly requires offer fields")
            actual = calls(body)
            require([item["tool"] for item in actual] == [item["tool"] for item in expected], "Law tool sequence differs")
            for observed, original in zip(actual, expected, strict=True):
                result = original["result"]
                expected_result = [copy.deepcopy(hit) | {"stub": True} for hit in result] if isinstance(result, list) \
                    else copy.deepcopy(result) | {"stub": True}
                require(observed["server"] == original["server"] and observed["args"] == original["args"]
                        and observed["error"] is None and observed["result"] == expected_result,
                        "Law call or preserved fixture result differs")
            require(bool(body["answer"]["citations"]) and all(e["source"] == "law" for e in body["answer"]["evidence"]),
                    "Law answer lacks law evidence")

    def unique_and_unknown_names(self):
        assert_lookup(self.query("해솔정밀에 대해 알려줘"), expected_ids={"SUP-0041"}, suppliers=self.suppliers,
                      expected_match_type="exact")
        assert_lookup(self.query("없는기업에 대해 알려줘"), expected_ids=set(), suppliers=self.suppliers,
                      expected_match_type="partial")

    def store_and_check(self, cid, query, previous, *, expected_kind=None):
        body = self.accepted(self.message(cid, query), selected=context(None), kind=expected_kind)
        self.forwarded(body, query, context(None), previous)
        snapshot = self.snapshot(cid)
        require(snapshot["context"] == context(None), "Follow-up invented selected UI context")
        require(snapshot["messages"][:-2] == previous and snapshot["messages"][-2]["content"] == query
                and snapshot["messages"][-1]["response"] == body, "Turn was not committed as a complete pair")
        self.assert_saved(snapshot, snapshot["messages"])
        return body, snapshot["messages"]

    def missing_company_resume(self):
        for reply in ("SUP-0041", "해솔정밀 (SUP-0041)"):
            cid = self.create(context(None))["conversation_id"]
            pending, previous = self.store_and_check(cid, FINANCIAL_QUERY, [], expected_kind="clarify")
            require(pending["routing"]["status"] == "not_routed" and calls(pending) == [], "Missing company caused a guessed tool call")
            resumed, _ = self.store_and_check(cid, reply, previous, expected_kind="answer")
            assert_resumed(resumed, reply, FINANCIAL_QUERY)
            assert_detail(resumed, "SUP-0041", self.suppliers)

    def ambiguous_company_resume(self):
        cid = self.create(context(None))["conversation_id"]
        pending, previous = self.store_and_check(cid, AMBIGUOUS_QUERY, [], expected_kind="clarify")
        assert_lookup(pending, expected_ids={"SUP-0041", "SUP-0060"}, suppliers=self.suppliers,
                      expected_match_type="partial")
        reply = "해솔정밀 (SUP-0041)"
        resumed, _ = self.store_and_check(cid, reply, previous, expected_kind="answer")
        assert_resumed(resumed, reply, AMBIGUOUS_QUERY)
        assert_detail(resumed, "SUP-0041", self.suppliers)

    def numbered_company_resume(self):
        cid = self.create(context(None))["conversation_id"]
        pending, previous = self.store_and_check(cid, AMBIGUOUS_QUERY, [], expected_kind="clarify")
        assert_lookup(pending, expected_ids={"SUP-0041", "SUP-0060"}, suppliers=self.suppliers,
                      expected_match_type="partial")
        options = pending["answer"]["options"]
        require("SUP-0041" in options[0] and "SUP-0060" in options[1], "Fixture candidate order changed")
        resumed, _ = self.store_and_check(cid, "2번", previous, expected_kind="answer")
        assert_resumed(resumed, "2번", AMBIGUOUS_QUERY)
        assert_detail(resumed, "SUP-0060", self.suppliers)

    def number_without_options_is_rejected(self):
        # Plain prose may contain names and apparent numbers. The stub must not
        # turn those into a selection without the explicit adapter JSON block.
        payload = {"request_id": str(uuid.uuid4()), "query": "2번", "context": context(None),
                   "history": [{"role": "user", "content": AMBIGUOUS_QUERY},
                               {"role": "assistant", "content": "1. 해솔정밀 (SUP-0041)\n2. 해솔금형 (SUP-0060)"}]}
        self.error(self.send(self.python, INTERNAL, payload, key=self.key), 400, "VALIDATION_ERROR", payload["request_id"])
        self.error(self.public(PUBLIC, payload), 400, "VALIDATION_ERROR", payload["request_id"])
        records = [record for record in self.relay.records if record["body"]["request_id"] == payload["request_id"]]
        require(len(records) == 1 and records[0]["authenticated"] and records[0]["body"] == payload,
                "Gateway invented option data for an unnumbered history")

    def topic_change(self):
        cid = self.create(context(None))["conversation_id"]
        _, previous = self.store_and_check(cid, FINANCIAL_QUERY, [], expected_kind="clarify")
        body, _ = self.store_and_check(cid, "프레스 협력사 찾아줘", previous, expected_kind="answer")
        require(body["decomposition"]["original_query"] == "프레스 협력사 찾아줘"
                and body["decomposition"]["sub_queries"][0]["intent"] == "supplier_search", "Topic change resumed an unrelated task")
        assert_search(body, {"process": "프레스"}, {"SUP-0052", "SUP-0060"}, self.suppliers)

    def tool_failure(self):
        port = unused_port(self.used_ports)
        env = child_environment(self.key)
        for name in ("LAW_MCP_URL", "BUSINESS_MCP_URL", "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL"):
            env[name] = f"http://127.0.0.1:{self.relay.port}/disabled"
        process = Process([str(ROOT / "agent/.venv/bin/python"), "-B", str(Path(__file__).resolve()),
                           "--serve-tool-failure", str(port)], ROOT / "agent", env, "Python caller-only error injection")
        self.processes.append(process)
        process.ready(port)
        previous_upstream = self.relay.upstream
        self.relay.upstream = port
        try:
            cid = self.create(context(None))["conversation_id"]
            body, _ = self.store_and_check(cid, "해솔정밀에 대해 알려줘", [], expected_kind="no_result")
            require(body["execution_status"] == "COMPLETED" and body["answer"]["options"] == [],
                    "Tool failure was confused with ambiguous company selection")
            assert_outcome(body, "error")
            require(any(call["error"] == "Tool execution failed." for call in calls(body)), "Tool diagnostic was not safely retained")
            require(TOOL_DIAGNOSTIC not in json.dumps(body), "Private tool exception leaked")
        finally:
            self.relay.upstream = previous_upstream

    def check_logs(self):
        self.check("logs.no_internal_key_or_tool_diagnostic", lambda: require(all(
            self.key not in "".join(process.log) and self.member_password not in "".join(process.log)
            and TOOL_DIAGNOSTIC not in "".join(process.log)
            for process in self.processes), "A process logged private diagnostic data"))

    def run(self):
        self.relay = GenerationRelay(self.key)
        self.used_ports.add(self.relay.port)
        self.python = self.start_fixture_python()
        self.relay.upstream = self.python
        self.spring = self.start_spring(self.key, "Spring direct-chat gateway")
        self.session = self.sign_in(self.spring)
        for name, fn in [
            ("selection_free_supplier_search", self.search_without_selection),
            ("selection_free_law_evidence", self.law_without_selection),
            ("unique_and_unknown_company_lookup", self.unique_and_unknown_names),
            ("missing_company_short_reply_resumes_financial_question", self.missing_company_resume),
            ("ambiguous_company_candidates_and_selection", self.ambiguous_company_resume),
            ("numbered_choice_preserves_options_order", self.numbered_company_resume),
            ("number_without_option_data_is_rejected", self.number_without_options_is_rejected),
            ("topic_change_after_clarification", self.topic_change),
            ("tool_failure_is_not_ambiguous_selection", self.tool_failure),
        ]:
            self.check(name, fn)
        self.check("no_live_model_or_external_tool_calls", lambda: require(not self.relay.unexpected_paths,
                   "An external dependency endpoint was called"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jar", type=Path, default=ROOT / "backend/target/negopilot-backend-0.1.0-SNAPSHOT.jar")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--serve-tool-failure", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve_tool_failure:
        import uvicorn
        uvicorn.run(tool_failure_app(), host="127.0.0.1", port=args.serve_tool_failure, access_log=False)
        return 0
    report_path = require_tmp_output(args.report)
    if not args.jar.is_file() or not CONTRACT.is_file() or not (ROOT / "agent/.venv/bin/python").is_file():
        print("Prepare the final Spring JAR, Python environment and OpenAPI before running.")
        return 2
    if shutil.which("docker") is None:
        print("Docker is required for the disposable PostgreSQL DB.")
        return 2
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="negopilot-direct-chat-", dir="/tmp") as temporary:
        jar = Path(temporary) / "backend.jar"
        shutil.copyfile(args.jar, jar)
        jar_hash = hashlib.sha256(jar.read_bytes()).hexdigest()
        runner = DirectChatSmoke(jar)
        try:
            runner.run()
        except KeyboardInterrupt:
            print("Cancelled. Cleaning up owned processes.", flush=True)
            return 130
        except Exception as error:
            runner.results.append({"name": "runner", "status": "FAIL", "detail":
                                   f"{type(error).__name__}: {runner.redact(str(error))}"})
        finally:
            runner.close()
        runner.check_logs()
        if runner.responses:
            runner.check("responses.match_fixed_openapi", runner.check_openapi)
        passed = sum(item["status"] == "PASS" for item in runner.results)
        report = {"schema_version": "0.3", "mode": "stub", "real_model": False,
                  "spring_jar_sha256": jar_hash, "duration_seconds": round(time.monotonic() - started, 2),
                  "passed": passed, "total": len(runner.results),
                  "http_responses_checked": len(runner.responses),
                  "checks": runner.results, "responses": runner.responses,
                  "forwarded": runner.relay.records if runner.relay else [],
                  "limitations": ["Built-in API stub questions with real graph and AnswerGenerator", "No real LLM/MCP calls",
                                  "Only tool-failure server overrides generation_caller",
                                  "Conversations use a disposable PostgreSQL container removed after the run"]}
        write_report(report_path, report, runner.key, runner.member_password)
        log_path = require_tmp_output(report_path.with_suffix(".logs.txt"))
        log_path.write_text("\n".join(p.label + "\n" + runner.redact("".join(p.log)) for p in runner.processes))
        print(f"Result: {passed}/{len(runner.results)} checks passed. Owned processes cleaned up.")
        return 0 if passed == len(runner.results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
