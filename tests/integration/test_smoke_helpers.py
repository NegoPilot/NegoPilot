"""Cost-free runner checks. Execute with the standard library unittest runner."""

import importlib.util
import copy
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import time
import unittest
import uuid
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("http_smoke", ROOT / "scripts/integration/http_smoke.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class SmokeHelpersTest(unittest.TestCase):
    def test_rejects_real_looking_tool_result_without_stub_marker(self):
        self.assertFalse(smoke.has_stub_label({"article_no": "제4조", "text": "법령 내용"}))
        self.assertTrue(smoke.has_stub_label({"source": "stub", "text": "테스트 데이터"}))

    def test_fixture_match_allows_defaults_but_rejects_changed_result(self):
        expected = {"sub_queries": [{"entities": {"law": "subcontract_act"}}]}
        actual = {"sub_queries": [{"entities": {"law": "subcontract_act", "as_of": None}}]}
        self.assertTrue(smoke.matches_subset(actual, expected))
        actual["sub_queries"][0]["entities"]["law"] = "changed"
        self.assertFalse(smoke.matches_subset(actual, expected))

    def test_child_environment_drops_inherited_credentials_and_selects_stub(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "inherited-secret",
                                     "OPENAI_API_KEY": "inherited-secret",
                                     "GOOGLE_APPLICATION_CREDENTIALS": "/unused/credentials.json",
                                     "AWS_ACCESS_KEY_ID": "inherited-secret",
                                     "PYTHONPATH": "/different/checkout",
                                     "AGENT_EXECUTION_MODE": "live",
                                     "AGENT_INTERNAL_API_KEY": "inherited-key"}):
            env = smoke.child_environment("ephemeral-key")
            no_key_env = smoke.child_environment(None)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", env)
        self.assertNotIn("AWS_ACCESS_KEY_ID", env)
        self.assertEqual(env["AGENT_INTERNAL_API_KEY"], "ephemeral-key")
        self.assertNotIn("AGENT_INTERNAL_API_KEY", no_key_env)
        self.assertEqual(env["AGENT_EXECUTION_MODE"], "stub")
        self.assertEqual(env["UV_NO_SYNC"], "1")
        self.assertEqual(env["PYTHONPATH"], os.pathsep.join((str(ROOT / "agent/src"), str(ROOT / "shared/src"))))
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")

    def test_uses_current_venv_without_dependency_installation(self):
        runner = smoke.Smoke(Path("unused.jar"))
        with patch.object(smoke, "Process") as process_class:
            runner.start_python("ephemeral-key", "mock startup")
        command = process_class.call_args.args[0]
        self.assertEqual(command[:4], [str(ROOT / "agent/.venv/bin/python"), "-B", "-m", "uvicorn"])
        self.assertNotIn("sync", command)
        process_class.return_value.ready.assert_called_once()

    def test_keyboard_interrupt_stops_children_and_skips_further_checks(self):
        with (patch.object(sys, "argv", ["http_smoke.py", "--jar", "/tmp/prepared-test.jar"]),
              patch.object(Path, "is_file", return_value=True),
              patch.object(Path, "glob", return_value=[]),
              patch.object(Path, "write_text") as write_report,
              patch.object(smoke, "Smoke") as runner_class,
              patch.object(smoke.subprocess, "Popen") as start_child,
              contextlib.redirect_stdout(io.StringIO())):
            runner = runner_class.return_value
            runner.run.side_effect = KeyboardInterrupt
            runner.responses = [{"path": smoke.PUBLIC_PATH, "status": 200, "body": {}}]
            self.assertEqual(smoke.main(), 130)
        runner.close.assert_called_once()
        runner.check_logs.assert_not_called()
        runner.check_openapi.assert_not_called()
        runner.check.assert_not_called()
        write_report.assert_not_called()
        start_child.assert_not_called()

    def test_current_routing_contract_accepts_distinct_tool_free_statuses(self):
        for status in ("general_knowledge", "out_of_scope"):
            with self.subTest(status=status):
                body = smoke.inject_boundary_case(self.execution_payload(), status)
                smoke.validate_execution_payload(body)
                self.assertEqual(body["routing"]["outcomes"][0]["status"], status)

    def test_referenced_article_error_preserves_ok_and_result_data(self):
        original = self.execution_payload()
        before = copy.deepcopy(original)
        response = smoke.inject_boundary_case(original, "reference_error")
        self.assertEqual(original, before)
        smoke.validate_execution_payload(response, sanitized=False)
        with self.assertRaises(AssertionError):
            smoke.validate_execution_payload(response)
        reference = response["routing"]["outcomes"][0]["referenced_articles"][0]
        reference["error"] = smoke.SAFE_TOOL_ERROR
        smoke.validate_execution_payload(response)
        self.assertEqual(reference["result"]["unchanged_text"], smoke.SYNTHETIC_DIAGNOSTIC)
        response["execution_status"] = "FAILED"
        response["error"] = {"code": "ROUTING_FAILED", "message": "Query routing failed.", "retryable": False}
        with self.assertRaises(AssertionError):
            smoke.validate_execution_payload(response)

    def test_diagnostics_require_fixed_text_but_result_text_is_unrestricted(self):
        response = smoke.inject_boundary_case(self.execution_payload(), "diagnostics")
        smoke.validate_execution_payload(response, sanitized=False)
        with self.assertRaises(AssertionError):
            smoke.validate_execution_payload(response)
        outcome = response["routing"]["outcomes"][0]
        outcome["calls"][0]["error"] = smoke.SAFE_TOOL_ERROR
        outcome["message"] = smoke.SAFE_OUTCOME_MESSAGE
        response["error"]["message"] = smoke.SAFE_EXECUTION_ERRORS["ROUTING_FAILED"]
        smoke.validate_execution_payload(response)
        self.assertEqual(outcome["calls"][0]["result"]["unchanged_text"], smoke.SYNTHETIC_DIAGNOSTIC)

    def test_current_routing_contract_rejects_obsolete_or_inconsistent_fields(self):
        def missing_schema(body):
            del body["routing"]["schema_version"]

        def missing_references(body):
            del body["routing"]["outcomes"][0]["referenced_articles"]

        def wrong_identity(body):
            body["routing"]["outcomes"][0]["id"] = "q2"

        def missing_args(body):
            body["routing"]["outcomes"][0]["status"] = "missing_args"
            body["execution_status"] = "NEEDS_INPUT"

        mutations = {
            "missing_schema": missing_schema,
            "missing_references": missing_references,
            "wrong_identity": wrong_identity,
            "missing_args_without_missing": missing_args,
            "no_tool": lambda b: b["routing"]["outcomes"][0].update(status="no_tool"),
            "ok_without_calls": lambda b: b["routing"]["outcomes"][0].update(calls=[]),
            "unexpected_missing": lambda b: b["routing"]["outcomes"][0].update(missing=["supplier"]),
            "wrong_reference_type": lambda b: b["routing"]["outcomes"][0].update(referenced_articles={}),
            "unpaired_halt": lambda b: b["routing"].update(halted=True),
            "wrong_schema": lambda b: b["routing"].update(schema_version="0.2"),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label):
                response = self.execution_payload()
                mutate(response)
                with self.assertRaises(AssertionError):
                    smoke.validate_execution_payload(response)

    def test_missing_arguments_and_error_priority_follow_outcome_status(self):
        response = self.execution_payload()
        first = response["routing"]["outcomes"][0]
        first.update(status="missing_args", calls=[], missing=["supplier"])
        response["execution_status"] = "NEEDS_INPUT"
        smoke.validate_execution_payload(response)
        second_query = copy.deepcopy(response["decomposition"]["sub_queries"][0])
        second_query["id"] = "q2"
        response["decomposition"]["sub_queries"].append(second_query)
        second = copy.deepcopy(self.execution_payload()["routing"]["outcomes"][0])
        second.update(id="q2", status="error")
        second["calls"][0]["error"] = smoke.SAFE_TOOL_ERROR
        response["routing"]["outcomes"].append(second)
        response["execution_status"] = "FAILED"
        response["error"] = {"code": "ROUTING_FAILED", "message": "Query routing failed.", "retryable": False}
        smoke.validate_execution_payload(response)

    @staticmethod
    def execution_payload():
        fixture = json.loads((ROOT / "shared/fixtures/decomposition/01_law_article.json").read_text())
        return {
            "schema_version": "0.1", "request_id": str(uuid.uuid4()), "execution_status": "COMPLETED",
            "generation_status": "NOT_IMPLEMENTED", "mode": "stub", "decomposition": fixture["expected"],
            "routing": {"schema_version": "0.1", "status": "routed", "halted": False, "halt_reason": None,
                        "outcomes": [{"id": "q1", "intent": "law_article", "status": "ok", "missing": [],
                                      "message": None, "referenced_articles": [], "calls": [{
                                          "server": "law-mcp", "tool": "get_article",
                                          "args": {"law": "subcontract_act", "article_no": "제4조"},
                                          "result": {"source": "stub"}, "error": None}]}]},
            "error": None,
        }

    def test_error_checker_detects_lost_id_and_wrong_retry_semantics(self):
        runner = smoke.Smoke(Path("unused.jar"))
        payload = {"request_id": "expected", "error": {
            "code": "UPSTREAM_TIMEOUT", "message": "Timed out", "retryable": True}}
        runner.error((504, payload), 504, "UPSTREAM_TIMEOUT", "expected", True)
        with self.assertRaises(AssertionError):
            runner.error((504, payload), 504, "UPSTREAM_TIMEOUT", "different", True)
        with self.assertRaises(AssertionError):
            runner.error((504, payload), 504, "UPSTREAM_TIMEOUT", "expected", False)

    def test_relay_records_no_key_and_can_inject_a_bad_response(self):
        relay = smoke.Relay(0, "ephemeral-key")
        try:
            relay.mode = "invalid_schema"
            payload = {"request_id": "test-id", "history": [{"role": "user", "content": "test"}]}
            status, _ = smoke.request(relay.port, smoke.INTERNAL_PATH, payload, key="ephemeral-key")
            self.assertEqual(status, 200)
            self.assertEqual(relay.records[0]["body"], payload)
            self.assertTrue(relay.records[0]["authenticated"])
            self.assertNotIn("ephemeral-key", json.dumps(relay.records))
        finally:
            relay.close()
        self.assertFalse(relay.thread.is_alive())

    def test_cleanup_closes_pipe_when_child_ignores_termination(self):
        child = ("import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                 "print('child-ready', flush=True); time.sleep(30)")
        parent = ("import subprocess,sys,time; "
                  f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)")
        process = smoke.Process([sys.executable, "-B", "-c", parent], ROOT,
                                smoke.child_environment(None), "cleanup regression")
        try:
            deadline = time.monotonic() + 5
            while not any("child-ready" in line for line in process.log) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(any("child-ready" in line for line in process.log))
            process.close()
            self.assertIsNotNone(process.process.returncode)
            self.assertFalse(process.reader.is_alive())
        finally:
            process.close()


if __name__ == "__main__":
    unittest.main()
