"""Helper regressions without starting either application or calling a model."""

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
SPEC = importlib.util.spec_from_file_location("supplier_chat_smoke", ROOT / "scripts/integration/supplier_chat_smoke.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def completed_response():
    return {
        "schema_version": "0.2", "mode": "stub", "request_id": str(uuid.uuid4()),
        "conversation_id": str(uuid.uuid4()), "response_id": str(uuid.uuid4()),
        "execution_status": "COMPLETED", "generation_status": "NOT_IMPLEMENTED",
        "resolved_context": smoke.context("SUP-0034"),
        "presentation": {"status": "READY", "renderer": "template", "text": "테스트 템플릿 응답",
                         "cards": [{"type": "supplier_detail", "supplier": copy.deepcopy(smoke.SUPPLIERS["SUP-0034"]),
                                    "evidence_ids": ["stub-evidence"]}]},
        "citations": [], "clarification": None,
        "evidence": [{"evidence_id": "stub-evidence", "server": "business-mcp", "tool": "get_supplier",
                      "supplier_id": "SUP-0034", "source": "stub", "executed": False}],
    }


class SupplierChatHelpersTest(unittest.TestCase):
    def test_history_keeps_latest_twenty_and_preserves_repeated_questions(self):
        messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"반복 질문 {i // 2}",
                     "message_id": str(i), "response": None} for i in range(24)]
        expected = [{"role": item["role"], "content": item["content"]} for item in messages[4:]]
        self.assertEqual(smoke.history(messages), expected)
        self.assertEqual(len(smoke.history(messages)), 20)

    def test_context_omission_does_not_turn_explicit_empty_id_into_no_selection(self):
        self.assertEqual(smoke.context(None), {"selected_supplier_ids": []})
        self.assertEqual(smoke.context(""), {"selected_supplier_ids": [""]})

    def test_default_report_stays_outside_checkout(self):
        self.assertEqual(smoke.DEFAULT_REPORT.parent, Path("/tmp"))

    def test_chat_check_rejects_changed_numbers_and_claimed_execution(self):
        runner = smoke.SupplierSmoke(Path("unused.jar"))
        body = completed_response()
        runner.assert_chat(body, body["conversation_id"], smoke.context("SUP-0034"))
        changed = copy.deepcopy(body)
        changed["presentation"]["cards"][0]["supplier"]["quality"]["on_time_delivery_rate"] = 9650
        with self.assertRaises(AssertionError):
            runner.assert_chat(changed, body["conversation_id"], smoke.context("SUP-0034"))
        changed = copy.deepcopy(body)
        changed["evidence"][0]["executed"] = True
        with self.assertRaises(AssertionError):
            runner.assert_chat(changed, body["conversation_id"], smoke.context("SUP-0034"))

    def test_response_mutations_do_not_change_original(self):
        body = completed_response()
        original = copy.deepcopy(body)
        for mode in ("request_id", "conversation_id", "resolved_context", "card_supplier",
                     "evidence_supplier", "evidence_reference", "status", "schema"):
            changed = smoke.corrupt_response(body, mode)
            self.assertNotEqual(changed, body)
            self.assertEqual(body, original)
        changed = smoke.corrupt_response(body, "card_supplier")
        changed["presentation"]["cards"][0]["supplier"]["name"] = "mutated test copy"
        self.assertNotEqual(smoke.SUPPLIERS["SUP-0052"]["name"], "mutated test copy")

    def test_chat_check_rejects_unknown_envelope_fields(self):
        runner = smoke.SupplierSmoke(Path("unused.jar"))
        body = completed_response()
        body["debug"] = "unexpected"
        with self.assertRaises(AssertionError):
            runner.assert_chat(body, body["conversation_id"], smoke.context("SUP-0034"))

    def test_needs_input_requires_empty_cards_and_precise_guidance(self):
        runner = smoke.SupplierSmoke(Path("unused.jar"))
        body = completed_response()
        body.update(execution_status="NEEDS_INPUT", resolved_context=smoke.context(None), evidence=[])
        body["presentation"].update(status="NEEDS_INPUT", text="기업을 선택해 주세요.", cards=[])
        body["clarification"] = {"missing_fields": ["context.selected_supplier_ids"],
                                 "question": body["presentation"]["text"], "options": smoke.QUESTIONS[:]}
        runner.assert_chat(body, body["conversation_id"], smoke.context(None), "context.selected_supplier_ids")
        changed = copy.deepcopy(body)
        changed["clarification"]["missing_fields"] = ["query"]
        with self.assertRaises(AssertionError):
            runner.assert_chat(changed, body["conversation_id"], smoke.context(None), "context.selected_supplier_ids")
        changed = copy.deepcopy(body)
        changed["presentation"]["cards"] = completed_response()["presentation"]["cards"]
        with self.assertRaises(AssertionError):
            runner.assert_chat(changed, body["conversation_id"], smoke.context(None), "context.selected_supplier_ids")

    def test_text_metrics_keep_percentage_units_and_large_won_values(self):
        runner = smoke.SupplierSmoke(Path("unused.jar"))
        body = completed_response()
        body["presentation"]["text"] = "새길다이캐스팅 테스트 12000000000원 900000000원 80% 96.5% 420ppm"
        runner.assert_text_metrics(body, smoke.QUESTIONS[0])
        body["presentation"]["text"] = body["presentation"]["text"].replace("96.5%", "9650%")
        with self.assertRaises(AssertionError):
            runner.assert_text_metrics(body, smoke.QUESTIONS[0])

    def test_saved_assistant_must_match_response_id_and_text(self):
        runner = smoke.SupplierSmoke(Path("unused.jar"))
        response = completed_response()
        messages = [{"role": "user", "content": "질문", "message_id": str(uuid.uuid4()), "response": None},
                    {"role": "assistant", "content": response["presentation"]["text"],
                     "message_id": response["response_id"], "response": response}]
        snapshot = {"storage": "memory", "messages": messages}
        runner.assert_saved(snapshot, messages)
        messages[1]["message_id"] = str(uuid.uuid4())
        with self.assertRaises(AssertionError):
            runner.assert_saved(snapshot, messages)

    def test_storage_rejects_unpaired_messages_and_unknown_fields(self):
        runner = smoke.SupplierSmoke(Path("unused.jar"))
        user = {"message_id": str(uuid.uuid4()), "role": "user", "content": "질문", "response": None}
        with self.assertRaises(AssertionError):
            runner.assert_saved({"storage": "memory", "messages": [user]}, [user])
        response = completed_response()
        assistant = {"message_id": response["response_id"], "role": "assistant",
                     "content": response["presentation"]["text"], "response": response, "debug": True}
        with self.assertRaises(AssertionError):
            runner.assert_saved({"storage": "memory", "messages": [user, assistant]}, [user, assistant])

    def test_canonical_uuid_check_rejects_noncanonical_forms(self):
        value = str(uuid.uuid4())
        self.assertTrue(smoke.canonical_uuid(value))
        self.assertFalse(smoke.canonical_uuid(value.replace("-", "")))
        self.assertFalse(smoke.canonical_uuid(None))

    def run_main_with_mock_runners(self, supplier, legacy):
        with tempfile.TemporaryDirectory(prefix="supplier-chat-helper-test-") as temporary:
            root = Path(temporary)
            jar = root / "backend.jar"
            jar.write_bytes(b"test-only JAR placeholder")
            contract = root / "contract.json"
            contract.write_text("{}")
            python = root / "agent/.venv/bin/python"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"")
            report = root / "report.json"
            with mock.patch.object(smoke, "ROOT", root), \
                    mock.patch.object(smoke, "CONTRACT", contract), \
                    mock.patch.object(smoke, "SupplierSmoke", return_value=supplier) as supplier_factory, \
                    mock.patch.object(smoke, "Smoke", return_value=legacy) as legacy_factory, \
                    mock.patch.object(sys, "argv", ["supplier_chat_smoke.py", "--jar", str(jar),
                                                   "--report", str(report)]), \
                    mock.patch("builtins.print"):
                exit_code = smoke.main()
            supplier_factory.assert_called_once()
            snapshot_jar = supplier_factory.call_args.args[0]
            self.assertFalse(snapshot_jar.exists(), "Cancelled run retained its private JAR snapshot")
            self.assertFalse(report.exists(), "Cancelled run wrote a success or failure report")
            return exit_code, legacy_factory

    def test_main_cancellation_closes_supplier_runner_without_starting_regression(self):
        supplier = mock.Mock(results=[], responses=[])
        supplier.run.side_effect = KeyboardInterrupt
        legacy = mock.Mock()
        exit_code, legacy_factory = self.run_main_with_mock_runners(supplier, legacy)
        self.assertEqual(exit_code, 130)
        supplier.close.assert_called_once_with()
        supplier.check_logs.assert_not_called()
        supplier.check.assert_not_called()
        legacy_factory.assert_not_called()
        legacy.run.assert_not_called()

    def test_main_cancellation_in_regression_closes_both_runners(self):
        supplier = mock.Mock(results=[], responses=[])
        supplier.check.side_effect = lambda _name, fn: fn()
        legacy = mock.Mock(results=[], responses=[])
        legacy.run.side_effect = KeyboardInterrupt
        exit_code, legacy_factory = self.run_main_with_mock_runners(supplier, legacy)
        self.assertEqual(exit_code, 130)
        supplier.close.assert_called_once_with()
        legacy_factory.assert_called_once()
        legacy.run.assert_called_once_with()
        legacy.close.assert_called_once_with()
        legacy.check_logs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
