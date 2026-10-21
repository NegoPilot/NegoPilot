"""Offline helper regressions. No application process or model is started."""
import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/integration"))
SPEC = importlib.util.spec_from_file_location("generation_smoke", ROOT / "scripts/integration/generation_smoke.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def execution_response(kind="answer", decomposition_status="ok"):
    body = {
        "schema_version": "0.3", "mode": "stub", "request_id": str(uuid.uuid4()),
        "execution_status": "COMPLETED", "generation_status": "COMPLETED", "context": smoke.context(None),
        "decomposition": {"status": decomposition_status, "original_query": "테스트 질문"},
        "routing": {"schema_version": "0.1", "status": "routed", "outcomes": [],
                    "halted": False, "halt_reason": None},
        "answer": {"kind": kind, "text": "테스트 답변", "citations": [], "options": [],
                   "grounded": False, "notices": [], "evidence": [],
                   "verification": {"attempts": 1, "unknown_citations": [], "ungrounded_numbers": [],
                                    "ungrounded_articles": [], "masked": False}},
        "error": None,
    }
    if kind == "clarify":
        body["execution_status"] = "NEEDS_INPUT"
    if kind == "failed":
        body.update(execution_status="FAILED", generation_status="FAILED")
        code = "DECOMPOSITION_FAILED" if decomposition_status == "failed" else "GENERATION_FAILED"
        message = "Query decomposition failed." if code == "DECOMPOSITION_FAILED" else "Answer generation failed."
        body["error"] = {"code": code, "message": message, "retryable": False}
    if decomposition_status in {"clarify", "failed"}:
        body["routing"]["status"] = "not_routed"
    return body


def cited_response():
    body = execution_response()
    evidence = {"id": "E1", "sub_query": "q1", "source": "law", "kind": "article", "label": "법령 근거",
                "text": "가상 법령 데이터", "citable": True, "source_url": "https://example.test/law", "meta": {}}
    body["answer"].update(grounded=True, evidence=[evidence], citations=[{
        "n": 1, "evidence_id": "E1", "label": evidence["label"], "source_url": evidence["source_url"]}])
    return body


class GenerationHelpersTest(unittest.TestCase):
    def test_history_uses_latest_twenty_and_counts_unicode_code_points(self):
        text = "🙂" * 4100
        messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": str(i)} for i in range(22)]
        messages[-1]["content"] = text
        before = copy.deepcopy(messages)
        actual = smoke.history(messages)
        self.assertEqual(len(actual), 20)
        self.assertEqual(actual[0], messages[2])
        self.assertEqual(actual[-1]["content"], "🙂" * 4000)
        self.assertEqual(messages, before, "History must not truncate the stored Answer")

    def test_history_has_only_model_fields_and_does_not_deduplicate_turns(self):
        messages = [{"role": "user", "content": "반복", "message_id": str(i), "response": None} for i in range(3)]
        self.assertEqual(smoke.history(messages), [{"role": "user", "content": "반복"}] * 3)

    def test_whitespace_only_history_prefix_uses_first_nonblank_slice_without_changing_storage(self):
        messages = [{"role": "assistant", "content": smoke.LONG_BLANK_PREFIX_TEXT}]
        before = copy.deepcopy(messages)
        self.assertEqual(smoke.history(messages), [{"role": "assistant", "content": "🙂" * 4000}])
        self.assertEqual(messages, before)

    def test_history_keeps_leading_spaces_when_first_slice_already_has_text(self):
        text = " " * 3999 + "가" + "나" * 100
        self.assertEqual(smoke.history([{"role": "assistant", "content": text}]),
                         [{"role": "assistant", "content": " " * 3999 + "가"}])

    def test_context_does_not_turn_invalid_empty_id_into_no_selection(self):
        self.assertEqual(smoke.context(None), {"selected_supplier_ids": []})
        self.assertEqual(smoke.context(""), {"selected_supplier_ids": [""]})

    def test_output_is_restricted_to_tmp(self):
        self.assertEqual(smoke.require_tmp_output(Path("/tmp/generation-test.json")),
                         Path("/tmp/generation-test.json").resolve())
        with self.assertRaises(AssertionError):
            smoke.require_tmp_output(ROOT / "generation-report.json")
        self.assertEqual(smoke.DEFAULT_REPORT.parent, Path("/tmp"))

    def test_all_answer_kinds_follow_fixed_contract_statuses(self):
        for kind in ("answer", "clarify", "refusal", "no_result", "failed"):
            with self.subTest(kind=kind):
                body = execution_response(kind)
                smoke.validate_generation(body, expected_kind=kind)
                body["execution_status"] = "FAILED" if kind != "failed" else "COMPLETED"
                with self.assertRaises(AssertionError):
                    smoke.validate_generation(body)
        smoke.validate_generation(execution_response("failed", "failed"))
        smoke.validate_generation(execution_response("clarify", "clarify"))

    def test_partial_routing_failure_does_not_overrule_final_answer(self):
        body = execution_response()
        body["routing"]["outcomes"] = [{"status": "error", "message": "Routing details are unavailable.",
            "calls": [{"error": "Tool execution failed.", "result": {"business": "preserved"}}],
            "referenced_articles": []}]
        smoke.validate_generation(body)
        body["execution_status"] = "FAILED"
        with self.assertRaises(AssertionError):
            smoke.validate_generation(body)

    def test_empty_raw_diagnostic_is_not_considered_safe_null(self):
        body = execution_response()
        body["routing"]["outcomes"] = [{"message": None, "calls": [{"error": ""}], "referenced_articles": []}]
        with self.assertRaises(AssertionError):
            smoke.validate_generation(body)
        body["routing"]["outcomes"][0]["calls"][0]["error"] = None
        smoke.validate_generation(body)

    def test_masked_verification_with_remaining_errors_is_a_valid_answer(self):
        body = execution_response()
        body["answer"]["verification"].update(masked=True, attempts=2, unknown_citations=["E999"],
                                               ungrounded_numbers=["7777777원"])
        body["answer"]["text"] = "[확인 필요]"
        smoke.validate_generation(body)
        body["answer"]["verification"]["ok"] = False
        with self.assertRaises(AssertionError):
            smoke.validate_generation(body)

    def test_citation_identity_and_provenance_must_match_citable_evidence(self):
        body = cited_response()
        smoke.validate_generation(body)
        for field, value in (("evidence_id", "E2"), ("label", "wrong"), ("source_url", None)):
            changed = copy.deepcopy(body)
            changed["answer"]["citations"][0][field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError):
                smoke.validate_generation(changed)
        changed = copy.deepcopy(body)
        changed["answer"]["evidence"][0]["citable"] = False
        with self.assertRaises(AssertionError):
            smoke.validate_generation(changed)

    def test_duplicate_evidence_or_citation_ids_are_rejected(self):
        body = cited_response()
        for group in ("evidence", "citations"):
            changed = copy.deepcopy(body)
            changed["answer"][group].append(copy.deepcopy(changed["answer"][group][0]))
            with self.subTest(group=group), self.assertRaises(AssertionError):
                smoke.validate_generation(changed)

    def test_mutations_are_explicit_and_do_not_modify_original(self):
        body = cited_response()
        before = copy.deepcopy(body)
        for mode in ("request_id", "original_query", "context", "citation", "evidence", "status", "schema"):
            self.assertNotEqual(smoke.corrupt_response(body, mode), before)
            self.assertEqual(body, before)
        with self.assertRaises(AssertionError):
            smoke.corrupt_response(body, "unknown")

    def test_envelope_requires_context_and_rejects_unknown_answer_fields(self):
        body = execution_response()
        with self.assertRaises(AssertionError):
            smoke.validate_generation(body, context_expected=smoke.context("SUP-0041"))
        del body["context"]
        with self.assertRaises(AssertionError):
            smoke.validate_generation(body)
        body = execution_response()
        body["answer"]["debug"] = "unknown"
        with self.assertRaises(AssertionError):
            smoke.validate_generation(body)

    def test_storage_keeps_complete_answer_and_matching_message_id(self):
        runner = smoke.GenerationSmoke(Path("unused.jar"))
        response = execution_response()
        response.update(conversation_id=str(uuid.uuid4()), response_id=str(uuid.uuid4()))
        response["answer"]["text"] = smoke.LONG_TEXT
        messages = [{"message_id": str(uuid.uuid4()), "role": "user", "content": "질문", "response": None},
                    {"message_id": response["response_id"], "role": "assistant", "content": smoke.LONG_TEXT,
                     "response": response}]
        runner.assert_saved({"messages": messages}, messages)
        changed = copy.deepcopy(messages)
        changed[-1]["content"] = smoke.LONG_TEXT[:4000]
        with self.assertRaises(AssertionError):
            runner.assert_saved({"messages": changed}, changed)

    def test_main_cancellation_closes_owned_runner_and_removes_jar_snapshot(self):
        runner = mock.Mock(results=[], responses=[])
        runner.run.side_effect = KeyboardInterrupt
        with tempfile.TemporaryDirectory(prefix="generation-helper-", dir="/tmp") as temporary:
            root = Path(temporary)
            jar = root / "source.jar"
            jar.write_bytes(b"fake JAR placeholder")
            contract = root / "contract.json"
            contract.write_text("{}")
            python = root / "agent/.venv/bin/python"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"")
            report = root / "report.json"
            with mock.patch.object(smoke, "ROOT", root), mock.patch.object(smoke, "CONTRACT", contract), \
                    mock.patch.object(smoke, "GenerationSmoke", return_value=runner) as factory, \
                    mock.patch.object(sys, "argv", ["generation_smoke.py", "--jar", str(jar), "--report", str(report)]), \
                    mock.patch("builtins.print"):
                self.assertEqual(smoke.main(), 130)
            runner.close.assert_called_once_with()
            runner.check_logs.assert_not_called()
            self.assertFalse(factory.call_args.args[0].exists())
            self.assertFalse(report.exists())

    def test_cleanup_attempts_every_owned_process_and_relay_after_one_failure(self):
        runner = smoke.GenerationSmoke(Path("unused.jar"))
        first = mock.Mock(label="first")
        last = mock.Mock(label="last")
        last.close.side_effect = RuntimeError("cleanup test " + runner.key)
        runner.processes = [first, last]
        runner.relay = mock.Mock()
        runner.close()
        runner.close()
        first.close.assert_called_once_with()
        last.close.assert_called_once_with()
        runner.relay.close.assert_called_once_with()
        self.assertEqual(len(runner.results), 1)
        self.assertEqual(runner.results[0]["name"], "cleanup.last")
        self.assertEqual(runner.results[0]["status"], "FAIL")
        self.assertNotIn(runner.key, runner.results[0]["detail"])


if __name__ == "__main__":
    unittest.main()
