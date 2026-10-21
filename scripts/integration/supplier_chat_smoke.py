#!/usr/bin/env python3
"""Exercise prepared local Spring/Python stub servers and retain v0.1 regression checks.

This runner never installs dependencies. It starts and closes only processes it
owns and writes its report to /tmp by default.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
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
from urllib.parse import quote, urlencode, urlsplit
import uuid

from http_smoke import ROOT, Smoke, child_environment, request, require


CHAT = "/internal/v1/chat/responses"
CONVERSATIONS = "/api/v1/conversations"
CONTRACT = ROOT / "docs/contracts/supplier-chat.openapi.json"
FIXTURE = json.loads((ROOT / "docs/contracts/fixtures/supplier-chat.json").read_text())
SUPPLIERS = {item["supplier_id"]: item for item in FIXTURE["suppliers"]}
QUESTIONS = FIXTURE["supported_queries"]
DEFAULT_REPORT = Path("/tmp/negopilot-supplier-chat-results.json")


def context(supplier_id: str | None) -> dict[str, list[str]]:
    return {"selected_supplier_ids": [] if supplier_id is None else [supplier_id]}


def history(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"role": item["role"], "content": item["content"]} for item in messages[-20:]]


def canonical_uuid(value: Any) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (TypeError, ValueError):
        return False


def corrupt_response(body: dict[str, Any], mode: str) -> dict[str, Any]:
    result = copy.deepcopy(body)
    if mode == "request_id":
        result["request_id"] = str(uuid.uuid4())
    elif mode == "conversation_id":
        result["conversation_id"] = str(uuid.uuid4())
    elif mode == "resolved_context":
        result["resolved_context"] = context("SUP-0052")
    elif mode == "card_supplier":
        result["presentation"]["cards"][0]["supplier"] = copy.deepcopy(SUPPLIERS["SUP-0052"])
    elif mode == "evidence_supplier":
        result["evidence"][0]["supplier_id"] = "SUP-0052"
    elif mode == "evidence_reference":
        result["presentation"]["cards"][0]["evidence_ids"] = ["missing-evidence"]
    elif mode == "status":
        result["presentation"]["status"] = "NEEDS_INPUT"
    elif mode == "schema":
        del result["generation_status"]
    return result


class SupplierRelay:
    """Forward only the v0.2 internal endpoints. Capture no credential values."""

    def __init__(self, key: str):
        self.key = key
        self.upstream = 0
        self.mode = "forward"
        self.records: list[dict[str, Any]] = []
        self.unexpected_paths: list[str] = []
        self.closed = False
        self.blocked_conversation: str | None = None
        self.first_chat_entered = threading.Event()
        self.release_first_chat = threading.Event()
        self.overlapping_chat_entered = threading.Event()
        self.gate_lock = threading.Lock()
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: Any) -> None:
                pass

            def do_GET(self) -> None:
                self.forward("GET")

            def do_POST(self) -> None:
                self.forward("POST")

            def forward(self, method: str) -> None:
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                path = urlsplit(self.path).path
                allowed = path == "/internal/v1/business/catalog" or path == "/internal/v1/suppliers" \
                    or path.startswith("/internal/v1/suppliers/") or path == CHAT
                if not allowed:
                    relay.unexpected_paths.append(path)
                    self.respond(503, b'{"error":"external dependencies disabled"}')
                    return
                payload = json.loads(raw) if raw else None
                relay.records.append({"method": method, "path": self.path, "body": payload,
                                      "authenticated": secrets.compare_digest(
                                          self.headers.get("X-Internal-Api-Key", ""), relay.key)})
                if path == CHAT and payload["conversation_id"] == relay.blocked_conversation:
                    with relay.gate_lock:
                        first = not relay.first_chat_entered.is_set()
                        if first:
                            relay.first_chat_entered.set()
                        else:
                            relay.overlapping_chat_entered.set()
                    if first:
                        relay.release_first_chat.wait(5)
                mode = relay.mode if path == CHAT else "forward"
                if mode == "invalid_json":
                    self.respond(200, b"invalid-json")
                    return
                headers = {"Content-Type": "application/json", "X-Internal-Api-Key":
                           "deliberately-invalid" if mode == "wrong_auth" else
                           self.headers.get("X-Internal-Api-Key", "")}
                conn = http.client.HTTPConnection("127.0.0.1", relay.upstream, timeout=8)
                try:
                    conn.request(method, self.path, body=raw or None, headers=headers)
                    response = conn.getresponse()
                    result = response.read()
                    if path == CHAT and response.status == 200 and mode != "forward":
                        result = json.dumps(corrupt_response(json.loads(result), mode)).encode()
                    self.respond(response.status, result)
                except (OSError, http.client.HTTPException):
                    self.respond(503, json.dumps({"request_id": (payload or {}).get("request_id"),
                                 "error": {"code": "UPSTREAM_UNAVAILABLE", "message": "Upstream unavailable",
                                           "retryable": True}}).encode())
                finally:
                    conn.close()

            def respond(self, status: int, body: bytes) -> None:
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
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
            self.release_first_chat.set()
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)


class SupplierSmoke(Smoke):
    def send(self, port: int, path: str, body: Any = None, **kwargs: Any) -> tuple[int, Any]:
        response = request(port, path, body, **kwargs)
        self.responses.append({"path": path, "method": kwargs.get("method", "POST"),
                               "status": response[0], "body": response[1]})
        require(self.key not in json.dumps(response[1]), "Response exposed the internal key")
        return response

    def public(self, path: str, body: Any = None, method: str = "GET", **kwargs: Any) -> tuple[int, Any]:
        return self.send(self.spring, path, body, method=method, **kwargs)

    def data(self, response: tuple[int, Any], status: int = 200) -> Any:
        code, body = response
        require(code == status, f"Expected HTTP {status} but received {code}")
        require(isinstance(body, dict), "Success response must be an object")
        require(body.get("schema_version") == "0.2" and body.get("mode") == "stub", "Missing v0.2 stub labels")
        return body

    def create(self, supplier_id: str | None) -> dict[str, Any]:
        body = self.data(self.public(CONVERSATIONS, {"context": context(supplier_id)}, "POST"), 201)
        require(canonical_uuid(body["conversation_id"]), "Conversation ID is not a canonical UUID")
        require(body["context"] == context(supplier_id) and body["messages"] == []
                and body["storage"] == "memory", "Incorrect initial conversation state")
        return body

    def snapshot(self, conversation_id: str) -> dict[str, Any]:
        return self.data(self.public(f"{CONVERSATIONS}/{conversation_id}"))

    def message(self, conversation_id: str, query: str, selected: Any = None) -> tuple[int, Any]:
        body = {"query": query}
        if selected is not None:
            body["context"] = selected
        return self.public(f"{CONVERSATIONS}/{conversation_id}/messages", body, "POST")

    def assert_chat(self, body: dict[str, Any], conversation_id: str, selected: dict[str, Any],
                    missing: str | None = None) -> None:
        self.data((200, body))
        require(set(body) == {"schema_version", "mode", "request_id", "conversation_id", "response_id",
                             "execution_status", "generation_status", "resolved_context", "presentation",
                             "citations", "evidence", "clarification"}, "Unexpected chat response fields")
        require(body["conversation_id"] == conversation_id and body["resolved_context"] == selected,
                "Response lost conversation or supplier context")
        require(canonical_uuid(body["request_id"]) and canonical_uuid(body["response_id"]),
                "Chat IDs are not canonical UUIDs")
        require(body["generation_status"] == "NOT_IMPLEMENTED" and body["citations"] == [],
                "Demo incorrectly claims generation or citations")
        presentation = body["presentation"]
        require(presentation["renderer"] == "template" and bool(presentation["text"].strip()),
                "Template response is missing")
        if missing:
            require(body["execution_status"] == "NEEDS_INPUT" and presentation["status"] == "NEEDS_INPUT",
                    "Incorrect clarification status")
            require(presentation["cards"] == [] and body["evidence"] == [], "Clarification invented evidence")
            require(body["clarification"]["missing_fields"] == [missing]
                    and body["clarification"]["question"] == presentation["text"]
                    and body["clarification"]["options"] == QUESTIONS, "Incorrect clarification guidance")
            return
        require(body["execution_status"] == "COMPLETED" and presentation["status"] == "READY"
                and body["clarification"] is None, "Incorrect completed state")
        require(len(presentation["cards"]) == 1 and len(body["evidence"]) == 1, "Incorrect card/evidence count")
        supplier_id = selected["selected_supplier_ids"][0]
        card, evidence = presentation["cards"][0], body["evidence"][0]
        require(card["type"] == "supplier_detail" and card["supplier"] == SUPPLIERS[supplier_id],
                "Card does not match the shared fixture")
        require(card["evidence_ids"] == [evidence["evidence_id"]] and bool(evidence["evidence_id"]),
                "Broken evidence reference")
        require(evidence["server"] == "business-mcp" and evidence["tool"] == "get_supplier"
                and evidence["supplier_id"] == supplier_id and evidence["source"] == "stub"
                and evidence["executed"] is False, "Incorrect stub provenance")

    def assert_saved(self, snapshot: dict[str, Any], expected: list[dict[str, Any]]) -> None:
        require(snapshot["storage"] == "memory" and snapshot["messages"] == expected[-100:],
                "Stored messages changed or retention is incorrect")
        require(len(snapshot["messages"]) <= 100 and len(snapshot["messages"]) % 2 == 0,
                "Stored history must contain at most fifty complete user/assistant pairs")
        require(len({item["message_id"] for item in snapshot["messages"]}) == len(snapshot["messages"]),
                "Stored message IDs are not unique")
        for index, item in enumerate(snapshot["messages"]):
            require(set(item) == {"message_id", "role", "content", "response"}, "Unexpected stored message fields")
            require(canonical_uuid(item["message_id"]), "Stored message has an invalid ID")
            require(item["role"] == ("user" if index % 2 == 0 else "assistant"), "Stored roles are not paired")
            if item["role"] == "user":
                require(item["response"] is None, "User message contains an assistant response")
            else:
                require(item["message_id"] == item["response"]["response_id"]
                        and item["content"] == item["response"]["presentation"]["text"], "Assistant storage differs from response")

    def assert_text_metrics(self, response: dict[str, Any], query: str) -> None:
        supplier = SUPPLIERS[response["resolved_context"]["selected_supplier_ids"][0]]
        text = response["presentation"]["text"].replace(",", "")
        require(supplier["name"] in text and any(label in text.casefold() for label in ("테스트", "가상", "stub")),
                "Template text lacks supplier identity or demo notice")
        expected = []
        if query != QUESTIONS[2]:
            for financial in supplier["financials"]:
                expected.extend([(financial["revenue_krw"], "원"), (financial["operating_profit_krw"], "원"),
                                 (financial["debt_ratio"], "%")])
        if query != QUESTIONS[1]:
            expected.extend([(supplier["quality"]["on_time_delivery_rate"], "%"),
                             (supplier["quality"]["defect_ppm"], "ppm")])
        for value, unit in expected:
            if value is not None:
                decimal = str(int(value)) if float(value).is_integer() else str(value)
                pattern = r"(?<![\d.])" + re.escape(decimal) + r"(?:\.0)?\s*" + re.escape(unit)
                require(re.search(pattern, text, re.IGNORECASE) is not None, "Template metric or unit differs from fixture")

    def assert_forwarded(self, response: dict[str, Any], query: str, previous: list[dict[str, Any]]) -> None:
        records = [r for r in self.relay.records if urlsplit(r["path"]).path == CHAT
                   and r["body"]["request_id"] == response["request_id"]]
        require(len(records) == 1, "Chat request was not forwarded exactly once")
        require(records[0]["authenticated"], "Server key was not used")
        require(records[0]["body"] == {"request_id": response["request_id"],
                "conversation_id": response["conversation_id"], "query": query,
                "context": response["resolved_context"], "history": history(previous)},
                "Forwarded query, history or context differs")

    def catalog_and_search(self) -> None:
        catalog = self.data(self.public("/api/v1/business/catalog"))["data"]
        expected: dict[str, set[str]] = {}
        for supplier in SUPPLIERS.values():
            for cap in supplier["capabilities"]:
                expected.setdefault(cap["process"], set()).add(cap["material"])
        actual = {item["process"]: set(item["materials"]) for item in catalog["processes"]}
        require(actual == expected and len(catalog["processes"]) == len(expected), "Catalog process/materials mismatch")
        expected_regions = {s["region"] for s in SUPPLIERS.values()}
        require(set(catalog["regions"]) == expected_regions
                and len(catalog["regions"]) == len(expected_regions), "Catalog regions mismatch")
        for params in (
            {"process": "다이캐스팅", "material": "ADC12"},
            {"process": " 다이캐스팅 ", "limit": "1"},
            {"process": "다이캐스팅", "region": "인천", "iatf_required": "true"},
            {"process": "프레스", "iatf_required": "false"},
            {"process": "프레스", "iatf_required": "true"},
            {"process": "존재하지 않는 공정"},
            {"process": "A+B% &?"},
        ):
            data = self.data(self.public("/api/v1/suppliers?" + urlencode(params)))["data"]
            expected_items = []
            for supplier in sorted(SUPPLIERS.values(), key=lambda item: item["supplier_id"]):
                matched = [cap for cap in supplier["capabilities"] if cap["process"] == params["process"].strip()
                           and (not params.get("material") or cap["material"] == params["material"])]
                if not matched or (params.get("region") and params["region"] != supplier["region"]) \
                        or (params.get("iatf_required") == "true" and not supplier["iatf16949"]):
                    continue
                expected_items.append({k: supplier[k] for k in
                                       ("supplier_id", "name", "region", "iatf16949", "provenance")} |
                                      {"matched_capabilities": matched})
            limit = int(params.get("limit", "20"))
            require(data == {"items": expected_items[:limit], "returned_count": len(expected_items[:limit]),
                             "limit": limit}, "Supplier filtering/grouping/limit mismatch")
        for supplier_id, supplier in SUPPLIERS.items():
            require(self.data(self.public(f"/api/v1/suppliers/{supplier_id}"))["data"] == supplier,
                    "Supplier detail differs from fixture")

    def happy_path(self) -> None:
        initial = self.create("SUP-0034")
        cid = initial["conversation_id"]
        previous: list[dict[str, Any]] = []
        ids: set[str] = set()
        for query in [*QUESTIONS, "이 회사의 재무 상태와 납기 지표를 알려줘"]:
            response = self.data(self.message(cid, " " + query + " "))
            self.assert_chat(response, cid, context("SUP-0034"))
            self.assert_text_metrics(response, query)
            self.assert_forwarded(response, query, previous)
            require(response["request_id"] not in ids, "Spring reused a request ID")
            ids.add(response["request_id"])
            current = self.snapshot(cid)
            require(current["context"] == initial["context"] and len(current["messages"]) == len(previous) + 2,
                    "Conversation context or message count changed")
            require(current["messages"][:-2] == previous and current["messages"][-2]["content"] == query
                    and current["messages"][-1]["response"] == response, "Response was not saved atomically")
            previous = current["messages"]
            self.assert_saved(current, previous)
        second = self.create("SUP-0041")
        require(second["conversation_id"] != cid, "Supplier change reused the conversation")
        response = self.data(self.message(second["conversation_id"], QUESTIONS[0]))
        self.assert_chat(response, second["conversation_id"], context("SUP-0041"))
        self.assert_text_metrics(response, QUESTIONS[0])
        self.assert_forwarded(response, QUESTIONS[0], [])
        require(self.snapshot(cid)["messages"] == previous, "New conversation changed previous history")
        self.primary = cid

    def clarification(self) -> None:
        for supplier_id, query, missing in ((None, "지원하지 않는 질문", "context.selected_supplier_ids"),
                                            ("SUP-0034", "지원하지 않는 질문", "query")):
            cid = self.create(supplier_id)["conversation_id"]
            response = self.data(self.message(cid, query))
            self.assert_chat(response, cid, context(supplier_id), missing)
            self.assert_forwarded(response, query, [])
            snapshot = self.snapshot(cid)
            require(len(snapshot["messages"]) == 2 and snapshot["messages"][1]["response"] == response,
                    "NEEDS_INPUT was not stored")
            self.assert_saved(snapshot, snapshot["messages"])

    def invalid_requests(self) -> None:
        for suffix in ("", "?process=", "?process=x&process=y", "?process=x&extra=y", "?process=x&limit=0",
                       "?process=x&limit=101", "?process=x&iatf_required=maybe", "?process=x&material="):
            self.error(self.public("/api/v1/suppliers" + suffix), 400, "VALIDATION_ERROR", exact_id=False)
        self.error(self.public("/api/v1/suppliers/SUP-NOT-FOUND"), 404, "SUPPLIER_NOT_FOUND", exact_id=False)
        for invalid_id in ("bad.id", "bad id", "기업-0034"):
            self.error(self.public("/api/v1/suppliers/" + quote(invalid_id, safe="")),
                       400, "VALIDATION_ERROR", exact_id=False)
        self.error(self.public(CONVERSATIONS + "/bad"), 400, "VALIDATION_ERROR", exact_id=False)
        self.error(self.public(CONVERSATIONS + "/" + str(uuid.uuid4())), 404, "CONVERSATION_NOT_FOUND", exact_id=False)
        self.error(self.public(CONVERSATIONS, {"context": context("SUP-NOT-FOUND")}, "POST"),
                   404, "SUPPLIER_NOT_FOUND", exact_id=False)
        for body in ({}, {"context": context("") | {"unknown": True}},
                     {"context": {"selected_supplier_ids": ["SUP-0034", "SUP-0034"]}},
                     {"context": {"selected_supplier_ids": ["SUP-0034", "SUP-0041"]}},
                     {"context": {"selected_supplier_ids": [" "]}},
                     {"context": context("foo/bar")}, {"context": context("bad id")},
                     {"context": context("기업-0034")}, {"context": context("")}):
            self.error(self.public(CONVERSATIONS, body, "POST"), 400, "VALIDATION_ERROR", exact_id=False)
        before = self.snapshot(self.primary)
        path = f"{CONVERSATIONS}/{self.primary}/messages"
        for body in ({"query": " "}, {"query": "x" * 4001}, {"query": QUESTIONS[0], "history": []},
                     {"query": QUESTIONS[0], "request_id": str(uuid.uuid4())}, {"query": 1},
                     {"query": QUESTIONS[0], "context": {"selected_supplier_ids": [" "]}}):
            self.error(self.public(path, body, "POST"), 400, "VALIDATION_ERROR", exact_id=False)
        self.error(self.public(path, method="POST", raw=b"{"), 400, "VALIDATION_ERROR", exact_id=False)
        self.error(self.message(self.primary, QUESTIONS[0], context("SUP-NOT-FOUND")),
                   404, "SUPPLIER_NOT_FOUND", exact_id=False)
        require(self.snapshot(self.primary) == before, "Invalid input changed saved conversation state")

    def authentication(self) -> None:
        internal = {"request_id": str(uuid.uuid4()), "conversation_id": str(uuid.uuid4()),
                    "query": QUESTIONS[0], "history": [], "context": context("SUP-0034")}
        for path, method, body in (("/internal/v1/business/catalog", "GET", None),
                                   ("/internal/v1/suppliers?" + urlencode({"process": "다이캐스팅"}), "GET", None),
                                   ("/internal/v1/suppliers/SUP-0034", "GET", None), (CHAT, "POST", internal)):
            for key in (None, "deliberately-invalid"):
                self.error(self.send(self.python, path, body, method=method, key=key), 401,
                           "UNAUTHORIZED", exact_id=False)
        response = self.data(self.public("/api/v1/business/catalog", key="untrusted-browser-value"))
        require(response["data"] and self.relay.records[-1]["authenticated"], "Spring trusted the browser's internal key")
        invalid = {**internal, "history": [{"role": "system", "content": "test"}]}
        self.error(self.send(self.python, CHAT, invalid, key=self.key), 400, "VALIDATION_ERROR", internal["request_id"])
        for invalid_id in ("foo/bar", "bad id", "기업-0034"):
            self.error(self.send(self.python, CHAT, {**internal, "context": context(invalid_id)}, key=self.key),
                       400, "VALIDATION_ERROR", internal["request_id"])
        self.error(self.send(self.python, "/internal/v1/suppliers/bad.id", method="GET", key=self.key),
                   400, "VALIDATION_ERROR", exact_id=False)

    def upstream_rollback(self) -> None:
        cid = self.create("SUP-0034")["conversation_id"]
        first = self.data(self.message(cid, QUESTIONS[0]))
        self.assert_chat(first, cid, context("SUP-0034"))
        before = self.snapshot(cid)
        for mode in ("request_id", "conversation_id", "resolved_context", "card_supplier", "evidence_supplier",
                     "evidence_reference", "status", "schema", "invalid_json", "wrong_auth"):
            self.relay.mode = mode
            try:
                count = len([r for r in self.relay.records if urlsplit(r["path"]).path == CHAT])
                self.error(self.message(cid, QUESTIONS[1], context("SUP-0041")), 502,
                           "UPSTREAM_INVALID_RESPONSE", exact_id=False)
                require(len([r for r in self.relay.records if urlsplit(r["path"]).path == CHAT]) == count + 1,
                        "Invalid upstream response was retried")
                require(self.snapshot(cid) == before, "Failed upstream call changed messages or context")
            finally:
                self.relay.mode = "forward"
        changed = self.data(self.message(cid, QUESTIONS[2], context("SUP-0041")))
        self.assert_chat(changed, cid, context("SUP-0041"))
        self.assert_forwarded(changed, QUESTIONS[2], before["messages"])
        require(self.snapshot(cid)["context"] == context("SUP-0041"), "Successful explicit context update was lost")

    def history_retention(self) -> None:
        cid = self.create("SUP-0034")["conversation_id"]
        messages: list[dict[str, Any]] = []
        for index in range(52):
            query = f"저장 경계 검사용 질문 {index}"
            response = self.data(self.message(cid, query))
            self.assert_chat(response, cid, context("SUP-0034"), "query")
            self.assert_forwarded(response, query, messages)
            snapshot = self.snapshot(cid)
            require(snapshot["messages"][-2]["content"] == query
                    and snapshot["messages"][-1]["response"] == response,
                    "Retention test stored a different query or response")
            messages.extend(snapshot["messages"][-2:])
            self.assert_saved(snapshot, messages)
        require(len(snapshot["messages"]) == 100 and snapshot["messages"][0]["content"] == "저장 경계 검사용 질문 2",
                "Retention did not remove the oldest complete user/assistant pairs")

    def serialized_requests(self) -> None:
        cid = self.create("SUP-0041")["conversation_id"]
        other = self.create("SUP-0034")["conversation_id"]
        self.relay.blocked_conversation = cid
        second_started = threading.Event()
        def second_request() -> dict[str, Any]:
            second_started.set()
            return self.data(self.message(cid, QUESTIONS[1]))
        with ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(lambda: self.data(self.message(cid, QUESTIONS[0])))
            try:
                require(self.relay.first_chat_entered.wait(2), "First chat never reached the relay")
                second = pool.submit(second_request)
                require(second_started.wait(1), "Second concurrent request did not start")
                require(not self.relay.overlapping_chat_entered.wait(0.3),
                        "Same-conversation requests reached Python concurrently")
                independent = pool.submit(lambda: self.data(self.message(other, QUESTIONS[0])))
                other_response = independent.result(timeout=1)
                self.assert_chat(other_response, other, context("SUP-0034"))
                require(not first.done(), "Blocked request ended before independence was observed")
            finally:
                self.relay.release_first_chat.set()
                self.relay.blocked_conversation = None
            responses = [first.result(timeout=4), second.result(timeout=4)]
        snapshot = self.snapshot(cid)
        require(len(snapshot["messages"]) == 4, "Concurrent requests lost stored messages")
        self.assert_saved(snapshot, snapshot["messages"])
        by_id = {response["response_id"]: response for response in responses}
        for offset in (0, 2):
            user, assistant = snapshot["messages"][offset:offset + 2]
            response = by_id[assistant["message_id"]]
            self.assert_chat(response, cid, context("SUP-0041"))
            self.assert_forwarded(response, user["content"], snapshot["messages"][:offset])

    def connection_failure(self) -> None:
        before = self.snapshot(self.primary)
        self.relay.close()
        self.error(self.message(self.primary, QUESTIONS[0]), 503, "UPSTREAM_UNAVAILABLE", retryable=True, exact_id=False)
        require(self.snapshot(self.primary) == before, "Connection failure changed stored messages")

    def check_openapi(self) -> None:
        result = subprocess.run([str(ROOT / "agent/.venv/bin/python"), "-B",
                                 str(ROOT / "scripts/integration/check_openapi.py"), "--contract", str(CONTRACT)],
                                input=json.dumps(self.responses), text=True, capture_output=True, cwd=ROOT,
                                env=child_environment(None), timeout=20)
        require(result.returncode == 0, "OpenAPI validation failed: " + result.stdout.strip())

    def check_logs(self) -> None:
        self.check("logs.no_internal_key", lambda:
                   require(all(self.key not in "".join(p.log) for p in self.processes),
                           "A child process logged the internal key"))
        self.check("logs.no_supplier_chat_history", lambda:
                   require(all("저장 경계 검사용 질문" not in "".join(p.log) for p in self.processes),
                           "A child process logged chat history"))

    def run(self) -> bool:
        self.relay = SupplierRelay(self.key)
        self.used_ports.add(self.relay.port)
        self.python = self.start_python(self.key, "Python supplier stub")
        self.relay.upstream = self.python
        self.spring = self.start_spring(self.key, "Spring supplier gateway")
        for name, fn in (("catalog_search_detail", self.catalog_and_search), ("conversation_flow", self.happy_path),
                         ("clarification_storage", self.clarification), ("validation_and_not_found", self.invalid_requests),
                         ("internal_authentication", self.authentication), ("upstream_mismatch_rollback", self.upstream_rollback),
                         ("history_window_and_retention", self.history_retention),
                         ("conversation_serialization_and_independence", self.serialized_requests),
                         ("connection_failure_rollback", self.connection_failure)):
            self.check(name, fn)
        self.check("no_model_or_mcp_requests", lambda:
                   require(not self.relay.unexpected_paths, "An external dependency endpoint was called"))
        return all(item["status"] == "PASS" for item in self.results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jar", type=Path, default=ROOT / "backend/target/negopilot-backend-0.1.0-SNAPSHOT.jar")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT,
                        help="Summary JSON output (default: /tmp/negopilot-supplier-chat-results.json)")
    args = parser.parse_args()
    if not args.jar.is_file() or not CONTRACT.is_file():
        print("Prepare the final Spring package and v0.2 OpenAPI contract before running.")
        return 2
    if not (ROOT / "agent/.venv/bin/python").is_file():
        print("Prepare agent/.venv before running. This runner does not install dependencies.")
        return 2
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="negopilot-supplier-smoke-") as temporary:
        # Run a private snapshot so another build cannot replace the open JAR.
        jar = Path(temporary) / "backend.jar"
        shutil.copyfile(args.jar, jar)
        jar_hash = hashlib.sha256(jar.read_bytes()).hexdigest()
        runner = SupplierSmoke(jar)
        try:
            runner.run()
        except KeyboardInterrupt:
            print("Cancelled. Cleaning up owned processes.", flush=True)
            return 130
        except Exception as error:
            runner.results.append({"name": "runner", "status": "FAIL", "detail":
                                   f"{type(error).__name__}: {str(error).replace(runner.key, '[REDACTED]')}"})
            print("FAIL runner: " + runner.results[-1]["detail"], flush=True)
        finally:
            runner.close()
        runner.check_logs()
        if runner.responses:
            runner.check("responses.match_supplier_chat_openapi", runner.check_openapi)
        legacy_report: dict[str, Any] | None = None

        def regression() -> None:
            nonlocal legacy_report
            # Keep cleanup in this process. Killing a timed-out wrapper would
            # otherwise strand the application sessions owned by that wrapper.
            legacy = Smoke(jar)
            legacy_started = time.monotonic()
            try:
                legacy.run()
            except Exception as error:
                legacy.results.append({"name": "runner", "status": "FAIL", "detail":
                                       f"{type(error).__name__}: {str(error).replace(legacy.key, '[REDACTED]')}"})
            finally:
                legacy.close()
            legacy.check_logs()
            if legacy.responses:
                legacy.check("responses.match_openapi", legacy.check_openapi)
            count = sum(item["status"] == "PASS" for item in legacy.results)
            legacy_report = {"mode": "stub", "duration_seconds": round(time.monotonic() - legacy_started, 2),
                             "spring_jar_sha256": jar_hash, "passed": count, "total": len(legacy.results),
                             "checks": legacy.results, "limitations": ["No live LLM/MCP/DB/mail calls"]}
            require(count == len(legacy.results), "v0.1 regression failed. See v0_1_regression in the report")
        try:
            runner.check("v0_1_http_regression", regression)
        except KeyboardInterrupt:
            print("Cancelled. Owned processes cleaned up.", flush=True)
            return 130
        passed = sum(item["status"] == "PASS" for item in runner.results)
        report = {"schema_version": "0.2", "mode": "stub", "spring_jar_sha256": jar_hash,
                  "duration_seconds": round(time.monotonic() - started, 2),
                  "passed": passed, "total": len(runner.results), "http_responses_checked": len(runner.responses),
                  "checks": runner.results, "v0_1_regression": legacy_report,
                  "limitations": ["No live LLM/MCP/DB/mail calls",
                    "Only fixture templates are supported", "History observed at Python HTTP boundary",
                    "Memory persistence ends when Spring exits"]}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"Result: {passed}/{len(runner.results)} checks passed. Owned processes and JAR snapshot cleaned up.")
        return 0 if passed == len(runner.results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
