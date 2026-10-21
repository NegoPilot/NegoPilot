"""Direct-chat smoke helper checks without starting servers or calling models."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/integration"))
SPEC = importlib.util.spec_from_file_location("direct_chat_smoke", ROOT / "scripts/integration/direct_chat_smoke.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)

SUPPLIERS = {
    "SUP-0041": {"supplier_id": "SUP-0041", "name": "해솔정밀", "region": "부산", "iatf16949": True,
                 "financials": [], "existing_contract": None,
                 "capabilities": [{"process": "다이캐스팅", "material": "ADC12", "monthly_capacity": 12}]},
    "SUP-0060": {"supplier_id": "SUP-0060", "name": "해솔금형", "region": "경남", "iatf16949": False,
                 "financials": [], "existing_contract": None,
                 "capabilities": [{"process": "프레스", "material": "SPCC", "monthly_capacity": 20}]},
}


def call(tool, args, result):
    return {"server": "business-mcp", "tool": tool, "args": args, "result": result, "error": None}


def response(tool_calls, *, kind="answer", options=None):
    status = {"clarify": "missing_args", "no_result": "empty"}.get(kind, "ok")
    return {"routing": {"outcomes": [{"status": status, "calls": tool_calls}]},
            "execution_status": "NEEDS_INPUT" if kind == "clarify" else "COMPLETED",
            "answer": {"kind": kind, "options": options or [], "citations": [{"evidence_id": "E1"}],
                       "evidence": [{"id": "E1"}]}}


def lookup_response(ids, *, match_type="partial"):
    candidates = [{k: SUPPLIERS[sid][k] for k in ("supplier_id", "name", "region")} for sid in ids]
    lookup = call("find_suppliers", {"supplier": "해솔", "limit": 10},
                  {"matches": candidates, "total": len(ids), "match_type": match_type})
    if len(ids) == 1:
        return response([lookup, call("get_supplier", {"supplier": ids[0]}, copy.deepcopy(SUPPLIERS[ids[0]]))])
    if ids:
        return response([lookup], kind="clarify", options=[f"{SUPPLIERS[sid]['name']} ({sid})" for sid in ids])
    return response([lookup], kind="no_result")


class DirectChatHelpersTest(unittest.TestCase):
    def test_fixture_extension_contains_unique_extra_supplier(self):
        suppliers = smoke.load_suppliers()
        self.assertEqual(set(suppliers), {"SUP-0034", "SUP-0041", "SUP-0052", "SUP-0060"})
        self.assertEqual(suppliers["SUP-0060"]["name"], "해솔금형")
        self.assertEqual(suppliers["SUP-0060"]["financials"], [])

    def test_search_checks_identity_capabilities_and_filter(self):
        supplier = SUPPLIERS["SUP-0060"]
        row = {k: supplier[k] for k in ("supplier_id", "name", "region", "iatf16949")} | supplier["capabilities"][0]
        body = response([call("search_suppliers", {"process": "프레스"}, [row])])
        smoke.assert_search(body, {"process": "프레스"}, {"SUP-0060"}, SUPPLIERS)
        for field, value in (("supplier_id", "SUP-UNKNOWN"), ("monthly_capacity", 999), ("process", "wrong")):
            changed = copy.deepcopy(body)
            changed["routing"]["outcomes"][0]["calls"][0]["result"][0][field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError):
                smoke.assert_search(changed, {"process": "프레스"}, {"SUP-0060"}, SUPPLIERS)
        body["routing"]["outcomes"][0]["status"] = "error"
        with self.assertRaises(AssertionError):
            smoke.assert_search(body, {"process": "프레스"}, {"SUP-0060"}, SUPPLIERS)

    def test_empty_search_is_not_a_company_clarification(self):
        body = response([call("search_suppliers", {"region": "제주"}, [])], kind="no_result")
        smoke.assert_search(body, {"region": "제주"}, set(), SUPPLIERS)
        for status in ("ok", "error"):
            body["routing"]["outcomes"][0]["status"] = status
            with self.subTest(status=status), self.assertRaises(AssertionError):
                smoke.assert_search(body, {"region": "제주"}, set(), SUPPLIERS)
        body["routing"]["outcomes"][0]["status"] = "empty"
        body["answer"]["kind"] = "clarify"
        with self.assertRaises(AssertionError):
            smoke.assert_search(body, {"region": "제주"}, set(), SUPPLIERS)

    def test_unique_name_requires_canonical_detail_and_preserves_null(self):
        body = lookup_response(["SUP-0041"], match_type="exact")
        smoke.assert_lookup(body, expected_ids={"SUP-0041"}, suppliers=SUPPLIERS, expected_match_type="exact")
        changed = copy.deepcopy(body)
        changed["routing"]["outcomes"][0]["status"] = "error"
        with self.assertRaises(AssertionError):
            smoke.assert_lookup(changed, expected_ids={"SUP-0041"}, suppliers=SUPPLIERS)
        changed = copy.deepcopy(body)
        changed["routing"]["outcomes"][0]["calls"][-1]["result"]["existing_contract"] = {}
        with self.assertRaises(AssertionError):
            smoke.assert_lookup(changed, expected_ids={"SUP-0041"}, suppliers=SUPPLIERS)
        changed["routing"]["outcomes"][0]["calls"][-1]["result"].pop("existing_contract")
        with self.assertRaises(AssertionError):
            smoke.assert_lookup(changed, expected_ids={"SUP-0041"}, suppliers=SUPPLIERS)
        body["routing"]["outcomes"][0]["calls"].pop()
        with self.assertRaises(AssertionError):
            smoke.assert_lookup(body, expected_ids={"SUP-0041"}, suppliers=SUPPLIERS)

    def test_canonical_id_detail_does_not_require_repeated_name_discovery(self):
        body = response([call("get_supplier", {"supplier": "SUP-0041"}, copy.deepcopy(SUPPLIERS["SUP-0041"]))])
        smoke.assert_detail(body, "SUP-0041", SUPPLIERS)
        body["routing"]["outcomes"][0]["calls"][0]["args"]["supplier"] = "해솔"
        with self.assertRaises(AssertionError):
            smoke.assert_detail(body, "SUP-0041", SUPPLIERS)

    def test_ambiguous_candidates_keep_real_names_and_ids_in_flexible_option_text(self):
        body = lookup_response(["SUP-0041", "SUP-0060"])
        body["answer"]["options"] = ["부산 해솔정밀 선택 (SUP-0041)", "경남 해솔금형 선택 (SUP-0060)"]
        smoke.assert_lookup(body, expected_ids={"SUP-0041", "SUP-0060"}, suppliers=SUPPLIERS)
        body["answer"]["options"][1] = "해솔금형"
        with self.assertRaises(AssertionError):
            smoke.assert_lookup(body, expected_ids={"SUP-0041", "SUP-0060"}, suppliers=SUPPLIERS)

    def test_ambiguous_lookup_must_not_guess_one_detail(self):
        body = lookup_response(["SUP-0041", "SUP-0060"])
        body["routing"]["outcomes"][0]["calls"].append(call("get_supplier", {"supplier": "SUP-0041"}, SUPPLIERS["SUP-0041"]))
        with self.assertRaises(AssertionError):
            smoke.assert_lookup(body, expected_ids={"SUP-0041", "SUP-0060"}, suppliers=SUPPLIERS)

    def test_lookup_count_limit_and_match_type_are_checked(self):
        body = lookup_response(["SUP-0041", "SUP-0060"])
        for section, field, value in (("args", "limit", 1), ("result", "total", 1), ("result", "match_type", "exact")):
            changed = copy.deepcopy(body)
            changed["routing"]["outcomes"][0]["calls"][0][section][field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError):
                smoke.assert_lookup(changed, expected_ids={"SUP-0041", "SUP-0060"}, suppliers=SUPPLIERS, expected_match_type="partial")

    def test_unknown_company_has_no_options_or_guessed_detail(self):
        body = lookup_response([])
        smoke.assert_lookup(body, expected_ids=set(), suppliers=SUPPLIERS, expected_match_type="partial")
        for status in ("ok", "error"):
            body["routing"]["outcomes"][0]["status"] = status
            with self.subTest(status=status), self.assertRaises(AssertionError):
                smoke.assert_lookup(body, expected_ids=set(), suppliers=SUPPLIERS)
        body["routing"]["outcomes"][0]["status"] = "empty"
        body["answer"]["options"] = ["해솔정밀 (SUP-0041)"]
        with self.assertRaises(AssertionError):
            smoke.assert_lookup(body, expected_ids=set(), suppliers=SUPPLIERS)

    def test_resume_keeps_current_query_and_original_task_in_separate_fields(self):
        body = {"decomposition": {"original_query": "SUP-0041", "sub_queries": [{
            "intent": "supplier_detail", "question": "해솔정밀 기업에 대한 요청: " + smoke.FINANCIAL_QUERY}]}}
        smoke.assert_resumed(body, "SUP-0041", smoke.FINANCIAL_QUERY)
        changed = copy.deepcopy(body)
        changed["decomposition"]["original_query"] = smoke.FINANCIAL_QUERY
        with self.assertRaises(AssertionError):
            smoke.assert_resumed(changed, "SUP-0041", smoke.FINANCIAL_QUERY)
        body["decomposition"]["sub_queries"][0]["question"] = "해솔정밀 개요"
        with self.assertRaises(AssertionError):
            smoke.assert_resumed(body, "SUP-0041", smoke.FINANCIAL_QUERY)

    def test_default_output_is_tmp_only(self):
        self.assertEqual(smoke.DEFAULT_REPORT.parent, Path("/tmp"))

    def test_forwarded_history_requires_actual_option_order_and_number_block(self):
        options = ['해솔정밀 "부산" (SUP-0041)', '해솔금형\n경남 (SUP-0060)']
        previous = [{"role": "user", "content": smoke.AMBIGUOUS_QUERY},
                    {"role": "assistant", "content": "어느 기업인가요?",
                     "response": {"answer": {"options": options}}}]
        numbered = [{"number": i, "text": value} for i, value in enumerate(options, 1)]
        forwarded = [{"role": "user", "content": smoke.AMBIGUOUS_QUERY},
                     {"role": "assistant", "content": "어느 기업인가요?" + smoke.OPTIONS_SEPARATOR
                      + json.dumps(numbered, ensure_ascii=False)}]
        smoke.assert_forwarded_history(forwarded, previous)
        for corrupted in (list(reversed(numbered)), numbered[:1],
                          [{"number": 1, "text": options[1]}, {"number": 2, "text": options[0]}],
                          [{"number": True, "text": options[0]}, numbered[1]]):
            changed = copy.deepcopy(forwarded)
            changed[1]["content"] = "어느 기업인가요?" + smoke.OPTIONS_SEPARATOR + json.dumps(corrupted)
            with self.subTest(corrupted=corrupted), self.assertRaises(AssertionError):
                smoke.assert_forwarded_history(changed, previous)
        forwarded[1]["content"] = "어느 기업인가요?"
        with self.assertRaises(AssertionError):
            smoke.assert_forwarded_history(forwarded, previous)

    def test_history_reserves_codepoints_for_options_without_changing_stored_answer(self):
        options = ["해솔정밀 (SUP-0041)", "해솔금형 (SUP-0060)"]
        suffix = smoke.OPTIONS_SEPARATOR + json.dumps([
            {"number": i, "text": value} for i, value in enumerate(options, 1)
        ], ensure_ascii=False, separators=(",", ":"))
        previous = [{"role": "assistant", "content": "😀" * 4000,
                     "response": {"answer": {"options": options}}}]
        original = copy.deepcopy(previous)
        forwarded = [{"role": "assistant", "content": "😀" * (4000 - len(suffix)) + suffix}]
        smoke.assert_forwarded_history(forwarded, previous)
        self.assertEqual(previous, original)
        forwarded[0]["content"] = "😀" + forwarded[0]["content"]
        with self.assertRaises(AssertionError):
            smoke.assert_forwarded_history(forwarded, previous)

    def test_report_redacts_nested_failed_responses_relay_and_errors_without_mutation(self):
        key = "temporary-internal-key-for-report-test"
        report = {"responses": [{"body": {"answer": {"text": "leaked " + key}}}],
                  "forwarded": [{"body": {"nested": [key]}, "upstream_response": {"key": key}}],
                  "checks": [{"status": "FAIL", "detail": "error with " + key}]}
        original = copy.deepcopy(report)
        with tempfile.TemporaryDirectory(prefix="direct-chat-report-helper-", dir="/tmp") as temporary:
            path = Path(temporary) / "nested/report.json"
            smoke.write_report(path, report, key)
            saved = path.read_text(encoding="utf-8")
            self.assertNotIn(key, saved)
            self.assertEqual(saved.count("[REDACTED]"), 4)
            self.assertEqual(json.loads(saved)["checks"][0]["status"], "FAIL")
        self.assertEqual(report, original)

    def test_cancel_closes_runner_without_writing_result(self):
        runner = mock.Mock(results=[], responses=[])
        runner.run.side_effect = KeyboardInterrupt
        with tempfile.TemporaryDirectory(prefix="direct-chat-helper-", dir="/tmp") as temporary:
            root = Path(temporary)
            jar = root / "source.jar"
            jar.write_bytes(b"JAR placeholder")
            contract = root / "contract.json"
            contract.write_text("{}")
            python = root / "agent/.venv/bin/python"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"")
            report = root / "report.json"
            with mock.patch.object(smoke, "ROOT", root), mock.patch.object(smoke, "CONTRACT", contract), \
                    mock.patch.object(smoke, "DirectChatSmoke", return_value=runner) as factory, \
                    mock.patch.object(sys, "argv", ["direct_chat_smoke.py", "--jar", str(jar), "--report", str(report)]), \
                    mock.patch("builtins.print"):
                self.assertEqual(smoke.main(), 130)
            runner.close.assert_called_once_with()
            runner.check_logs.assert_not_called()
            self.assertFalse(report.exists())
            self.assertFalse(factory.call_args.args[0].exists())


if __name__ == "__main__":
    unittest.main()
