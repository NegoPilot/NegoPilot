#!/usr/bin/env python3
"""Prepared Spring/Python Generation integration with fake model/tool boundaries.

The fixture server executes QueryDecomposer, build_graph and AnswerGenerator.
Spring runs its default profile against a disposable PostgreSQL container and
the runner signs in as an approved member with a CSRF token. No dependency is
installed and no existing process is discovered or stopped. Artifacts are
restricted to /tmp. A separate live-model run is not implied.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
import uuid

from smoke_common import ROOT, Process, Smoke, SpringSession, child_environment, request, require, unused_port

INTERNAL = "/internal/v2/query-executions"
PUBLIC = "/api/v2/agent/queries"
CONVERSATIONS = "/api/v2/conversations"
CONTRACT = ROOT / "docs/contracts/agent-generation.openapi.json"
DEFAULT_REPORT = Path("/tmp/negopilot-generation-smoke-results.json")
DIAGNOSTIC = "private-generation-tool-diagnostic"
LONG_TEXT = "🙂" * 4105 + " 긴 테스트 본문입니다."
LONG_BLANK_PREFIX_TEXT = " " * 4000 + "🙂" * 4105 + " 공백 접두부 뒤의 테스트 본문입니다."
SNAPSHOT_WINDOW = 50
STORED_FIELDS = {"seq", "message_id", "role", "content", "created_at", "response"}
CASES = [
    ("GEN_LAW", "answer", "COMPLETED"),
    ("GEN_OFFER", "answer", "COMPLETED"),
    ("GEN_CLARIFY", "clarify", "NEEDS_INPUT"),
    ("GEN_REFUSAL", "refusal", "COMPLETED"),
    ("GEN_NO_RESULT", "no_result", "COMPLETED"),
    ("GEN_DECOMPOSITION_FAILED", "failed", "FAILED"),
    ("GEN_GENERATION_FAILED", "failed", "FAILED"),
    ("GEN_PARTIAL", "answer", "COMPLETED"),
    ("GEN_MASKED", "answer", "COMPLETED"),
]


def context(supplier_id: str | None) -> dict[str, list[str]]:
    return {"selected_supplier_ids": [] if supplier_id is None else [supplier_id]}


def history(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    # Python slicing counts Unicode code points. Stored assistant text is intact.
    result = []
    for item in messages[-20:]:
        content = item["content"]
        if item["role"] == "assistant":
            prefix = content[:4000]
            content = prefix if prefix.strip() else content.lstrip()[:4000]
        result.append({"role": item["role"], "content": content})
    return result


def canonical_uuid(value: Any) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def require_tmp_output(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    require(resolved.is_relative_to(Path("/tmp").resolve()), "Output artifacts must stay under /tmp")
    return resolved


def validate_generation(body: dict[str, Any], *, context_expected: Any = None,
                        expected_kind: str | None = None) -> None:
    fields = {"schema_version", "request_id", "mode", "execution_status", "generation_status", "context",
              "decomposition", "routing", "answer", "error"}
    require(isinstance(body, dict) and set(body) in (fields, fields | {"conversation_id", "response_id"}),
            "Unexpected Generation envelope fields")
    require(body["schema_version"] == "0.3" and body["mode"] in {"stub", "live"}, "Incorrect Generation labels")
    require(canonical_uuid(body["request_id"]), "Invalid request ID")
    if "conversation_id" in body:
        require(canonical_uuid(body["conversation_id"]) and canonical_uuid(body["response_id"]), "Invalid message IDs")
    if context_expected is not None:
        require(body["context"] == context_expected, "Resolved context changed")
    answer = body["answer"]
    require(isinstance(answer, dict) and set(answer) == {
        "kind", "text", "citations", "options", "grounded", "notices", "evidence", "verification"},
        "Incomplete Answer serialization")
    kind = answer["kind"]
    require(kind in {"answer", "clarify", "refusal", "no_result", "failed"}, "Invalid Answer kind")
    if expected_kind is not None:
        require(kind == expected_kind, "Unexpected Answer kind")
    require(isinstance(answer["text"], str) and bool(answer["text"].strip()), "Missing Answer text")
    require(type(answer["grounded"]) is bool, "grounded must be boolean")
    expected_status = "NEEDS_INPUT" if kind == "clarify" else "FAILED" if kind == "failed" else "COMPLETED"
    require(body["execution_status"] == expected_status, "Execution must follow final Answer kind")
    require(body["generation_status"] == ("FAILED" if kind == "failed" else "COMPLETED"), "Generation status mismatch")
    if kind == "failed":
        code = "DECOMPOSITION_FAILED" if body["decomposition"]["status"] == "failed" else "GENERATION_FAILED"
        message = "Query decomposition failed." if code == "DECOMPOSITION_FAILED" else "Answer generation failed."
        require(body["error"] == {"code": code, "message": message, "retryable": False}, "Incorrect Generation failure")
    else:
        require(body["error"] is None, "Completed Answer incorrectly has top-level error")
    evidence = answer["evidence"]
    citations = answer["citations"]
    require(isinstance(evidence, list) and isinstance(citations, list), "Missing evidence/citations arrays")
    ids = [item["id"] for item in evidence]
    require(len(ids) == len(set(ids)), "Duplicate evidence IDs")
    by_id = {item["id"]: item for item in evidence}
    numbers = [item["n"] for item in citations]
    require(len(numbers) == len(set(numbers)) and all(type(n) is int and n > 0 for n in numbers),
            "Invalid or duplicate citation numbers")
    cited = [item["evidence_id"] for item in citations]
    require(len(cited) == len(set(cited)), "Duplicate citation evidence IDs")
    for citation in citations:
        source = by_id.get(citation["evidence_id"])
        require(source is not None and source["citable"] is True, "Citation references absent/non-citable evidence")
        require(citation["label"] == source["label"] and citation["source_url"] == source["source_url"],
                "Citation provenance differs from evidence")
    verification = answer["verification"]
    if verification is not None:
        require(set(verification) == {"attempts", "unknown_citations", "ungrounded_numbers",
                                      "ungrounded_articles", "masked"}, "Verification fields changed")
        require(type(verification["masked"]) is bool and type(verification["attempts"]) is int,
                "Invalid verification values")
    routing = body["routing"]
    require(routing["schema_version"] == "0.1", "Missing Routing version")
    for outcome in routing["outcomes"]:
        require("referenced_articles" in outcome, "Missing reference array")
        if outcome["message"] is not None:
            require(outcome["message"] == "Routing details are unavailable.", "Unsafe outcome diagnostic")
        for call in outcome["calls"] + outcome["referenced_articles"]:
            if call["error"] is not None:
                require(call["error"] == "Tool execution failed.", "Unsafe tool diagnostic")
    if routing["halt_reason"] is not None:
        require(routing["halt_reason"] == "Routing halted because a dependency returned no results.", "Unsafe halt reason")
    if body["decomposition"]["status"] in {"clarify", "failed"}:
        require(routing == {"schema_version": "0.1", "status": "not_routed", "outcomes": [],
                            "halted": False, "halt_reason": None}, "Skipped Routing defaults are missing")


def corrupt_response(body: dict[str, Any], mode: str) -> dict[str, Any]:
    result = copy.deepcopy(body)
    if mode == "request_id":
        result["request_id"] = str(uuid.uuid4())
    elif mode == "original_query":
        # Deliberately corrupt only the boundary copy after real graph execution.
        result["decomposition"]["original_query"] = "Synthetic mismatched original query"
    elif mode == "context":
        result["context"] = context("SUP-WRONG")
    elif mode == "citation":
        result["answer"]["citations"][0]["evidence_id"] = "missing-evidence"
    elif mode == "evidence":
        result["answer"]["evidence"].append(copy.deepcopy(result["answer"]["evidence"][0]))
    elif mode == "status":
        result["execution_status"] = "FAILED"
    elif mode == "schema":
        del result["answer"]["verification"]
    else:
        raise AssertionError("Unknown synthetic boundary fault")
    return result


class GenerationRelay:
    """Observe request bodies and unmodified Python responses. Never record keys."""
    def __init__(self, key: str):
        self.key = key
        self.upstream = 0
        self.mode = "forward"
        self.records: list[dict[str, Any]] = []
        self.unexpected_paths: list[str] = []
        self.gate_query: str | None = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.closed = False
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: Any) -> None:
                pass

            def do_GET(self) -> None:
                relay.unexpected_paths.append(self.path)
                self.respond(503, {"error": "External dependency disabled"})

            def do_POST(self) -> None:
                if self.path != INTERNAL:
                    relay.unexpected_paths.append(self.path)
                    self.respond(503, {"error": "External dependency disabled"})
                    return
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                record = {"path": self.path, "body": payload, "authenticated": secrets.compare_digest(
                    self.headers.get("X-Internal-Api-Key", ""), relay.key)}
                relay.records.append(record)
                if payload["query"] == relay.gate_query:
                    relay.entered.set()
                    relay.release.wait(8)
                mode = relay.mode
                if mode == "invalid_json":
                    self.respond(200, b"invalid-json")
                    return
                if mode == "http_error":
                    self.respond(503, {"request_id": payload["request_id"], "error": {
                        "code": "UPSTREAM_UNAVAILABLE", "message": "Synthetic transport failure.", "retryable": True}})
                    return
                status, data = request(relay.upstream, INTERNAL, payload,
                                       key="deliberately-invalid" if mode == "wrong_auth" else relay.key, timeout=10)
                record["upstream_status"] = status
                record["upstream_response"] = copy.deepcopy(data)
                if status == 200 and mode != "forward":
                    data = corrupt_response(data, mode)
                self.respond(status, data)

            def respond(self, status: int, data: Any) -> None:
                raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.release.set()
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)


def fixture_app():
    """Only IO boundaries are fake. Real core classes and graph run in the API."""
    from negopilot_agent.api.app import create_app
    from negopilot_agent.api.settings import ApiSettings
    from negopilot_agent.decomposition import QueryDecomposer

    fixtures = ROOT / "shared/fixtures"
    mappings = {"GEN_LAW": "01_law_article", "GEN_OFFER": "05_offer_check",
                "GEN_CLARIFY": "09_clarify", "GEN_REFUSAL": "10_out_of_scope",
                "GEN_DECOMPOSITION_FAILED": "11_failed"}
    records = []
    for name in ("01_law_article", "05_offer_check"):
        route = json.loads((fixtures / "routing" / f"{name}.json").read_text())["routing"]
        records += [c for o in route["outcomes"] for c in o["calls"] + o["referenced_articles"]]

    def subquery(intent: str, *, qid="q1", entities=None):
        return {"id": qid, "question": "테스트 질문", "original_text": "테스트 질문", "intent": intent,
                "grounding": "none" if intent in {"general_knowledge", "out_of_scope"} else "required",
                "confidence": 0.99, "entities": entities or {}, "inputs": [], "depends_on": []}

    class DecompositionBackend:
        async def generate(self, system, user):
            query = user.split("분해할 질문:\n", 1)[1].split("\n\n직전 출력", 1)[0]
            selection_match = re.search(r'선택 기업 문맥[^\n]*\n([^\n]+)', user)
            selected = json.loads(selection_match.group(1)) if selection_match else context(None)
            if query in mappings:
                value = json.loads((fixtures / "decomposition" / (mappings[query] + ".json")).read_text())["expected"]
                return json.dumps(value, ensure_ascii=False)
            if query == "GEN_NO_RESULT":
                queries = [subquery("draft_email")]
            elif query == "GEN_PARTIAL":
                queries = [subquery("rfq_lookup"), subquery("law_article", qid="q2", entities={
                    "law": "subcontract_act", "article_no": "제4조"})]
            elif query in {"GEN_CONTEXT", "GEN_EXPLICIT_SUP-0041"}:
                supplier = "SUP-0041" if query == "GEN_EXPLICIT_SUP-0041" else next(iter(selected["selected_supplier_ids"]), None)
                queries = [subquery("supplier_detail", entities={"supplier": supplier} if supplier else {})]
            else:
                queries = [subquery("general_knowledge")]
            return json.dumps({"schema_version": "0.1", "status": "ok", "original_query": query,
                               "sub_queries": queries, "clarification": None, "assumptions": []}, ensure_ascii=False)

    class GenerationBackend:
        async def generate(self, system, user):
            query = re.search(r"<question>(.*?)</question>", user, re.S).group(1)
            if query == "GEN_GENERATION_FAILED":
                return None
            if query == "GEN_LONG":
                text, used = LONG_TEXT, []
            elif query == "GEN_LONG_BLANK_PREFIX":
                text, used = LONG_BLANK_PREFIX_TEXT, []
            elif query == "GEN_MASKED":
                text, used = "근거 없는 금액 7777777원입니다. [E999]", ["E999"]
            else:
                match = re.search(r'<e id="(E\d+)"[^\n]*citable="yes"', user)
                used = [match.group(1)] if match else []
                text = "가짜 모델이 작성한 통합 검사 답변입니다." + (f" [{used[0]}]" if used else "")
            return json.dumps({"answer": text, "used_evidence": used}, ensure_ascii=False)

    async def caller(server, tool, args):
        if tool == "list_rfqs":
            raise RuntimeError(DIAGNOSTIC)
        if tool == "get_supplier":
            return {"supplier_id": args["supplier"], "name": "가상 검증 기업", "source": "stub"}
        for item in records:
            if (item["server"], item["tool"], item["args"]) == (server, tool, args):
                return copy.deepcopy(item["result"])
        raise AssertionError("Unrecorded fake tool request")

    return create_app(settings=ApiSettings.from_env(),
                      generation_decomposer=QueryDecomposer(backend=DecompositionBackend()),
                      generation_caller=caller, generation_backend=GenerationBackend())


class GenerationSmoke(Smoke):
    def __init__(self, jar: Path):
        super().__init__(jar)
        self.used_ports.update({5173, 8080, 9200})

    def start_fixture_python(self) -> int:
        port = unused_port(self.used_ports)
        env = child_environment(self.key)
        env.update({"AGENT_REQUEST_TIMEOUT_SECONDS": "10"})
        for name in ("LAW_MCP_URL", "BUSINESS_MCP_URL", "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL"):
            env[name] = f"http://127.0.0.1:{self.relay.port}/disabled"
        process = Process([str(ROOT / "agent/.venv/bin/python"), "-B", str(Path(__file__).resolve()),
                           "--serve-fixture", str(port)], ROOT / "agent", env, "Python Generation fake dependencies")
        self.processes.append(process)
        process.ready(port)
        return port

    def send(self, port, path, body=None, **kwargs):
        response = request(port, path, body, **kwargs)
        if path != "/health":
            self.responses.append({"path": path, "method": kwargs.get("method", "POST"),
                                   "status": response[0], "body": response[1]})
        require(self.key not in json.dumps(response[1]), "Response exposed the internal key")
        return response

    def public(self, path, body=None, method="POST", *, headers=None, record=True):
        response = self.session.send(method, path, body, headers=headers, timeout=10)
        # The Generation OpenAPI documents query and message responses. Stored v0.4
        # snapshots are checked field by field in assert_saved instead.
        if record and method == "POST" and path != CONVERSATIONS:
            self.responses.append({"path": path, "method": method, "status": response[0], "body": response[1]})
        require(self.key not in json.dumps(response[1]), "Response exposed the internal key")
        return response

    def accepted(self, response, *, selected=None, kind=None):
        status, body = response
        require(status == 200, f"Expected HTTP 200 but received {status}")
        validate_generation(body, context_expected=selected, expected_kind=kind)
        require(body["mode"] == "stub", "Fake dependencies must be labelled stub")
        return body

    def forwarded(self, body, query, selected, previous):
        records = [r for r in self.relay.records if r["body"]["request_id"] == body["request_id"]]
        require(len(records) == 1 and records[0]["authenticated"], "Request forwarding/authentication mismatch")
        require(records[0]["body"] == {"request_id": body["request_id"], "query": query,
                                      "context": selected, "history": history(previous)}, "Forwarded context/history changed")
        upstream = records[0].get("upstream_response")
        public = {k: v for k, v in body.items() if k not in {"conversation_id", "response_id"}}
        require(upstream == public, "Spring changed Answer, evidence, citations or verification")
        require(body["decomposition"]["original_query"] == query, "Decomposition original query changed")

    def create(self, selected):
        status, body = self.public(CONVERSATIONS, {"context": selected})
        require(status == 201, "Conversation creation failed")
        require(body["schema_version"] == "0.4" and body["storage"] == "database" and body["title"] == "새 대화"
                and body["message_count"] == 0 and body["context"] == selected and body["messages"] == []
                and body["next_cursor"] is None, "Invalid initial snapshot")
        require(canonical_uuid(body["conversation_id"]), "Invalid conversation ID")
        return body

    def snapshot(self, cid):
        status, body = self.public(f"{CONVERSATIONS}/{cid}", method="GET")
        require(status == 200, "Snapshot lookup failed")
        return body

    def message(self, cid, query, selected=None, *, key=None, record=True):
        payload = {"query": query}
        if selected is not None:
            payload["context"] = selected
        headers = None if key is None else {"Idempotency-Key": key}
        return self.public(f"{CONVERSATIONS}/{cid}/messages", payload, headers=headers, record=record)

    def older_messages(self, cid, before_seq):
        status, body = self.public(f"{CONVERSATIONS}/{cid}/messages?before_seq={before_seq}&size=100", method="GET")
        require(status == 200 and body["schema_version"] == "0.4" and body["conversation_id"] == cid,
                "Older message page lookup failed")
        return body

    def assert_saved(self, snapshot, expected):
        # v0.4 keeps every pair. The snapshot shows only the latest 50 messages.
        messages = snapshot["messages"]
        require(snapshot["message_count"] == len(expected) and len(expected) % 2 == 0
                and messages == expected[-SNAPSHOT_WINDOW:], "Stored messages or snapshot window changed")
        older = len(expected) > SNAPSHOT_WINDOW
        require(snapshot["next_cursor"] == (str(messages[0]["seq"]) if older else None), "Snapshot cursor is wrong")
        require([item["seq"] for item in messages] == list(range(len(expected) - len(messages) + 1, len(expected) + 1)),
                "Stored seq is not contiguous")
        require(len({m["message_id"] for m in messages}) == len(messages), "Duplicate stored message IDs")
        for item in messages:
            require(set(item) == STORED_FIELDS, "Unexpected stored fields")
            require(canonical_uuid(item["message_id"]), "Invalid stored message ID")
            require(item["role"] == ("user" if item["seq"] % 2 == 1 else "assistant"), "Stored roles are unpaired")
            if item["role"] == "user":
                require(item["response"] is None, "User response must be null")
            else:
                require(item["message_id"] == item["response"]["response_id"]
                        and item["content"] == item["response"]["answer"]["text"], "Stored Answer was altered")

    def execution_cases(self):
        for query, kind, execution_status in CASES:
            selected = context(None)
            payload = {"request_id": str(uuid.uuid4()), "query": query, "history": [], "context": selected}
            direct = self.accepted(self.send(self.python, INTERNAL, payload, key=self.key), selected=selected, kind=kind)
            public = self.accepted(self.public(PUBLIC, payload), selected=selected, kind=kind)
            require(public == direct, "Python and Spring executions differ")
            require(public["execution_status"] == execution_status, "Fixed expected status failed")
            self.forwarded(public, query, selected, [])
            if query in {"GEN_LAW", "GEN_OFFER", "GEN_PARTIAL"}:
                require(bool(public["answer"]["citations"]) and bool(public["answer"]["evidence"]), "Missing generated citation")
            if query == "GEN_PARTIAL":
                require([o["status"] for o in public["routing"]["outcomes"]] == ["error", "ok"]
                        and bool(public["answer"]["notices"]), "Partial routing failure was lost")
                require(DIAGNOSTIC not in json.dumps(public), "Private tool diagnostic leaked")
            if query == "GEN_MASKED":
                report = public["answer"]["verification"]
                require(report["masked"] and report["attempts"] == 2 and report["unknown_citations"] == ["E999"]
                        and "7777777원" in report["ungrounded_numbers"], "Generator verification did not execute")
                require("[확인 필요]" in public["answer"]["text"], "Unverified number was not masked")
        # Stateless query input preserves whitespace. Conversation message input
        # is intentionally trimmed and is checked by context_and_storage.
        query = "  GEN_GENERAL  "
        payload = {"request_id": str(uuid.uuid4()), "query": query, "history": [], "context": context(None)}
        public = self.accepted(self.public(PUBLIC, payload), kind="answer")
        self.forwarded(public, query, context(None), [])

    def context_and_storage(self):
        selected = context("SUP-0034")
        cid = self.create(selected)["conversation_id"]
        previous = []
        for query, explicit in [("GEN_CONTEXT", None), ("GEN_EXPLICIT_SUP-0041", None),
                                ("GEN_CONTEXT", context("SUP-0041")), ("GEN_CLARIFY", context(None))]:
            resolved = selected if explicit is None else explicit
            body = self.accepted(self.message(cid, "  " + query + "  ", explicit), selected=resolved,
                                 kind="clarify" if query == "GEN_CLARIFY" else "answer")
            self.forwarded(body, query, resolved, previous)
            if query.startswith("GEN_CONTEXT") or query.startswith("GEN_EXPLICIT"):
                expected_id = "SUP-0041" if query == "GEN_EXPLICIT_SUP-0041" else resolved["selected_supplier_ids"][0]
                require(body["decomposition"]["sub_queries"][0]["entities"]["supplier"] == expected_id,
                        "Decomposer prompt lost selected context or explicit supplier")
            snap = self.snapshot(cid)
            require(snap["context"] == resolved and snap["messages"][:-2] == previous
                    and snap["messages"][-2]["content"] == query and snap["messages"][-1]["response"] == body,
                    "Successful message/context not committed together")
            previous = snap["messages"]
            self.assert_saved(snap, previous)
            selected = resolved
        self.primary = cid

    def rollback(self):
        cid = self.primary
        before = self.snapshot(cid)
        for query in ("GEN_DECOMPOSITION_FAILED", "GEN_GENERATION_FAILED"):
            self.accepted(self.message(cid, query, context("SUP-NEW")), kind="failed")
            require(self.snapshot(cid) == before, "FAILED execution changed stored state")
        for mode in ("http_error", "wrong_auth", "invalid_json", "request_id", "original_query", "context",
                     "citation", "evidence", "status", "schema"):
            self.relay.mode = mode
            try:
                response = self.message(cid, "GEN_LAW", context("SUP-NEW"))
                self.error(response, 503 if mode == "http_error" else 502,
                           "UPSTREAM_UNAVAILABLE" if mode == "http_error" else "UPSTREAM_INVALID_RESPONSE",
                           retryable=mode == "http_error", exact_id=False)
            finally:
                self.relay.mode = "forward"
            require(self.snapshot(cid) == before, "HTTP/boundary failure changed stored state")

    def long_history_retention(self):
        selected = context(None)
        cid = self.create(selected)["conversation_id"]
        body = self.accepted(self.message(cid, "GEN_LONG"), kind="answer")
        require(body["answer"]["text"] == LONG_TEXT, "Long generated text was truncated before storage")
        stored = self.snapshot(cid)["messages"]
        require(stored[-1]["content"] == LONG_TEXT, "Stored long Answer was truncated")
        for index in range(51):
            query = f"GEN_RETAIN_{index}"
            body = self.accepted(self.message(cid, query), kind="answer")
            self.forwarded(body, query, selected, stored)
            snap = self.snapshot(cid)
            require(snap["messages"][-2]["content"] == query and snap["messages"][-1]["response"] == body,
                    "Retention stored the wrong response")
            stored = stored + snap["messages"][-2:]
            self.assert_saved(snap, stored)
        require(len(stored) == 104 and stored[0]["content"] == "GEN_LONG", "Earlier pairs were not kept")
        page = self.older_messages(cid, snap["next_cursor"])
        require(page["messages"] == stored[:-SNAPSHOT_WINDOW] and page["next_cursor"] is None,
                "Older message page differs from the stored pairs")
        first = next(r for r in self.relay.records if r["body"]["query"] == "GEN_RETAIN_0")
        require(first["body"]["history"][1]["content"] == LONG_TEXT[:4000], "Unicode history was not truncated to 4000 code points")

    def blank_prefix_history(self):
        selected = context(None)
        cid = self.create(selected)["conversation_id"]
        body = self.accepted(self.message(cid, "GEN_LONG_BLANK_PREFIX"), kind="answer")
        require(body["answer"]["text"] == LONG_BLANK_PREFIX_TEXT,
                "Generator output with a blank prefix was rewritten")
        before = self.snapshot(cid)
        require(before["messages"][-1]["content"] == LONG_BLANK_PREFIX_TEXT,
                "Stored Answer lost its blank prefix")
        following = self.accepted(self.message(cid, "GEN_AFTER_BLANK_PREFIX"), kind="answer")
        self.forwarded(following, "GEN_AFTER_BLANK_PREFIX", selected, before["messages"])
        record = next(r for r in self.relay.records if r["body"]["request_id"] == following["request_id"])
        transmitted = record["body"]["history"][1]["content"]
        require(transmitted == "🙂" * 4000 and bool(transmitted.strip()),
                "Whitespace-only prefix was sent as an invalid history entry")
        after = self.snapshot(cid)
        require(after["messages"][:2] == before["messages"], "Sending history changed the stored Answer")
        self.assert_saved(after, after["messages"])

    def busy_conversation(self):
        selected = context(None)
        cid = self.create(selected)["conversation_id"]
        other = self.create(selected)["conversation_id"]
        self.relay.gate_query = "GEN_FIRST"
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.message, cid, "GEN_FIRST")
            try:
                require(self.relay.entered.wait(2), "First request never reached relay")
                # The 409 status is part of the history contract, not the Generation OpenAPI.
                self.error(self.message(cid, "GEN_SECOND", record=False), 409, "CONVERSATION_BUSY",
                           retryable=True, exact_id=False)
                self.accepted(self.message(other, "GEN_OTHER"), kind="answer")
                require(not first.done(), "Blocked request ended before the independent request")
            finally:
                self.relay.release.set()
                self.relay.gate_query = None
            self.accepted(first.result(timeout=5), kind="answer")
        require(not any(record["body"]["query"] == "GEN_SECOND" for record in self.relay.records),
                "A busy conversation request reached Python")
        snap = self.snapshot(cid)
        require([item["content"] for item in snap["messages"]][:1] == ["GEN_FIRST"], "Busy request was stored")
        self.assert_saved(snap, snap["messages"])

    def idempotent_retry(self):
        cid = self.create(context(None))["conversation_id"]
        key = str(uuid.uuid4())
        first = self.accepted(self.message(cid, "GEN_LAW", key=key), kind="answer")
        forwarded = len(self.relay.records)
        again = self.accepted(self.message(cid, "GEN_LAW", key=key), kind="answer")
        require(again == first and len(self.relay.records) == forwarded,
                "Retry with the same key called Python again or changed the saved response")
        self.error(self.message(cid, "GEN_OFFER", key=key, record=False), 409, "IDEMPOTENCY_CONFLICT", exact_id=False)
        snap = self.snapshot(cid)
        require(snap["message_count"] == 2, "Retry stored duplicate messages")
        self.assert_saved(snap, snap["messages"])

    def authentication_and_csrf(self):
        forwarded = len(self.relay.records)
        anonymous = SpringSession(self.spring)
        self.error(anonymous.send("GET", CONVERSATIONS), 401, "UNAUTHENTICATED", exact_id=False)
        anonymous.refresh_csrf()
        self.error(anonymous.send("POST", PUBLIC, {"query": "GEN_LAW"}), 401, "UNAUTHENTICATED", exact_id=False)
        self.error(self.session.send("POST", PUBLIC, {"query": "GEN_LAW"}, csrf=False), 403, "CSRF_INVALID",
                   exact_id=False)
        self.error(self.session.send("POST", CONVERSATIONS, {"context": context(None)}, csrf=False), 403,
                   "CSRF_INVALID", exact_id=False)
        require(len(self.relay.records) == forwarded, "A rejected request reached Python")

    def invalid_inputs(self):
        for body in ({"query": " "}, {"query": "x", "context": None},
                     {"query": "x", "history": None}, {"query": "x", "context": context("SUP BAD")},
                     {"query": "x", "unknown": True}):
            self.error(self.public(PUBLIC, body), 400, "VALIDATION_ERROR", exact_id=False)
        payload = {"request_id": str(uuid.uuid4()), "query": "GEN_LAW"}
        self.error(self.send(self.python, INTERNAL, payload), 401, "UNAUTHORIZED", payload["request_id"])
        self.error(self.public(f"{CONVERSATIONS}/{uuid.uuid4()}", method="GET"), 404,
                   "CONVERSATION_NOT_FOUND", exact_id=False)
        cid = self.create(context(None))["conversation_id"]
        before = self.snapshot(cid)
        for extra in ({"history": []}, {"request_id": str(uuid.uuid4())}, {"context": None}):
            self.error(self.public(f"{CONVERSATIONS}/{cid}/messages", {"query": "GEN_LAW"} | extra),
                       400, "VALIDATION_ERROR", exact_id=False)
        require(self.snapshot(cid) == before, "Rejected message changed conversation")

    def check_openapi(self):
        result = subprocess.run([str(ROOT / "agent/.venv/bin/python"), "-B",
                                 str(ROOT / "scripts/integration/check_openapi.py"), "--contract", str(CONTRACT)],
                                input=json.dumps(self.responses), text=True, capture_output=True,
                                cwd=ROOT, env=child_environment(None), timeout=30)
        require(result.returncode == 0, "Generation OpenAPI failed: " + result.stdout.strip())

    def run(self):
        self.relay = GenerationRelay(self.key)
        self.used_ports.add(self.relay.port)
        self.python = self.start_fixture_python()
        self.relay.upstream = self.python
        self.spring = self.start_spring(self.key, "Spring Generation gateway")
        self.session = self.sign_in(self.spring)
        for name, fn in [("graph_answer_kinds_and_partial_results", self.execution_cases),
                         ("context_history_and_successful_storage", self.context_and_storage),
                         ("failed_and_invalid_response_rollback", self.rollback),
                         ("long_history_20_window_and_full_storage", self.long_history_retention),
                         ("blank_prefix_history_preserves_full_stored_answer", self.blank_prefix_history),
                         ("busy_conversation_is_rejected", self.busy_conversation),
                         ("idempotent_retry_reuses_saved_response", self.idempotent_retry),
                         ("membership_authentication_and_csrf", self.authentication_and_csrf),
                         ("input_and_internal_authentication", self.invalid_inputs)]:
            self.check(name, fn)
        self.check("no_live_model_or_tool_requests", lambda: require(not self.relay.unexpected_paths,
                   "An external dependency endpoint was called"))

    def check_logs(self):
        self.check("logs.no_secrets_or_private_diagnostics", lambda: require(all(
            self.key not in "".join(p.log) and self.member_password not in "".join(p.log)
            and DIAGNOSTIC not in "".join(p.log) for p in self.processes),
            "A process logged private diagnostic data"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jar", type=Path, default=ROOT / "backend/target/negopilot-backend-0.1.0-SNAPSHOT.jar")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--serve-fixture", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve_fixture:
        import uvicorn
        uvicorn.run(fixture_app(), host="127.0.0.1", port=args.serve_fixture, access_log=False)
        return 0
    report_path = require_tmp_output(args.report)
    if not args.jar.is_file() or not CONTRACT.is_file() or not (ROOT / "agent/.venv/bin/python").is_file():
        print("Prepare Python environment, final Spring JAR and Generation OpenAPI before running.")
        return 2
    if shutil.which("docker") is None:
        print("Docker is required for the disposable PostgreSQL DB.")
        return 2
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="negopilot-generation-smoke-", dir="/tmp") as temporary:
        jar = Path(temporary) / "backend.jar"
        shutil.copyfile(args.jar, jar)
        jar_hash = hashlib.sha256(jar.read_bytes()).hexdigest()
        runner = GenerationSmoke(jar)
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
            runner.check("responses.match_generation_openapi", runner.check_openapi)
        passed = sum(item["status"] == "PASS" for item in runner.results)
        report = {"schema_version": "0.3", "mode": "stub", "real_model": False,
                  "spring_jar_sha256": jar_hash, "duration_seconds": round(time.monotonic() - started, 2),
                  "passed": passed, "total": len(runner.results), "http_responses_checked": len(runner.responses),
                  "checks": runner.results, "responses": runner.responses, "forwarded": runner.relay.records if runner.relay else [],
                  "limitations": ["Real graph and AnswerGenerator with fake model/tool IO", "No real LLM/MCP calls",
                                  "Fault modes only test gateway rollback",
                                  "Conversations use a disposable PostgreSQL container removed after the run"]}
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        logs_path = require_tmp_output(report_path.with_suffix(".logs.txt"))
        logs_path.write_text("\n".join(p.label + "\n" + runner.redact("".join(p.log)) for p in runner.processes))
        print(f"Result: {passed}/{len(runner.results)} checks passed. Owned processes cleaned up.")
        return 0 if passed == len(runner.results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
