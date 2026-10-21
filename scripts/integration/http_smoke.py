#!/usr/bin/env python3
"""Run the stub contract through real Spring and Python HTTP processes.

Only the standard library is needed by this runner. Dependencies and the Spring
executable JAR must already have been prepared by their owning teams.
"""

from __future__ import annotations

import argparse
import copy
import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
INTERNAL_PATH = "/internal/v1/query-executions"
PUBLIC_PATH = "/api/v1/agent/queries"
FIXTURES = (
    ("01_law_article.json", "COMPLETED"),
    ("09_clarify.json", "NEEDS_INPUT"),
    ("11_failed.json", "FAILED"),
)
OUTCOME_STATUSES = {"ok", "general_knowledge", "out_of_scope", "unsupported",
                    "missing_args", "empty", "skipped", "error"}
SAFE_TOOL_ERROR = "Tool execution failed."
SAFE_OUTCOME_MESSAGE = "Routing details are unavailable."
SAFE_HALT_REASON = "Routing halted because a dependency returned no results."
SAFE_EXECUTION_ERRORS = {"DECOMPOSITION_FAILED": "Query decomposition failed.",
                         "ROUTING_FAILED": "Query routing failed."}
SYNTHETIC_DIAGNOSTIC = "Synthetic diagnostic: demo-token-must-not-leak"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def matches_subset(actual: Any, expected: Any) -> bool:
    """Pydantic may add optional defaults when serializing a fixture."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and matches_subset(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            matches_subset(a, e) for a, e in zip(actual, expected)
        )
    return actual == expected


def has_stub_label(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            ("stub" in str(key).lower() and item is True) or has_stub_label(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(has_stub_label(item) for item in value)
    return isinstance(value, str) and (
        "stub" in value.lower() or "테스트" in value or "가짜" in value
    )


def validate_execution_payload(body: Any, *, sanitized: bool = True) -> None:
    """Check current Routing structure and cross-field HTTP invariants offline.

    This does not invoke the core Router or a model. JSON Schema supplies the
    remaining decomposition field checks when recorded responses are checked.
    """
    fields = {"schema_version", "request_id", "execution_status", "generation_status",
              "mode", "decomposition", "routing", "error"}
    require(isinstance(body, dict) and set(body) == fields, "Unexpected execution fields")
    require(body["schema_version"] == "0.1", "Incorrect envelope schema_version")
    require(body["generation_status"] == "NOT_IMPLEMENTED", "Incorrect generation_status")
    require(isinstance(body["mode"], str) and body["mode"] in {"stub", "live"}, "Incorrect execution mode")
    require(isinstance(body["request_id"], str), "Missing execution request_id")
    try:
        uuid.UUID(body["request_id"])
    except (ValueError, TypeError, AttributeError):
        raise AssertionError("Invalid execution request_id") from None
    decomposition = body["decomposition"]
    require(isinstance(decomposition, dict) and isinstance(decomposition.get("status"), str)
            and decomposition["status"] in {"ok", "clarify", "failed"},
            "Invalid decomposition status")
    sub_queries = decomposition.get("sub_queries")
    require(isinstance(sub_queries, list), "Missing decomposition sub_queries")
    routing = body["routing"]
    require(isinstance(routing, dict) and set(routing) == {
        "schema_version", "status", "outcomes", "halted", "halt_reason"}, "Unexpected Routing fields")
    require(routing["schema_version"] == "0.1", "Incorrect Routing schema_version")
    require(isinstance(routing["status"], str) and routing["status"] in {"routed", "not_routed"}, "Invalid Routing status")
    outcomes = routing["outcomes"]
    require(isinstance(outcomes, list) and len(outcomes) == len(sub_queries), "Routing outcome count mismatch")
    require(type(routing["halted"]) is bool, "Routing halted must be boolean")
    require(routing["halt_reason"] is None or isinstance(routing["halt_reason"], str), "Invalid halt reason")
    require(routing["halted"] == (routing["halt_reason"] is not None), "Inconsistent halt metadata")
    require(routing["status"] == ("routed" if decomposition["status"] == "ok" else "not_routed"),
            "Decomposition and Routing status disagree")
    require(bool(outcomes) == (routing["status"] == "routed"), "Routing status and outcomes disagree")
    seen = set()
    for index, outcome in enumerate(outcomes):
        require(isinstance(outcome, dict) and set(outcome) == {
            "id", "intent", "status", "calls", "referenced_articles", "missing", "message"},
            "Unexpected outcome fields")
        require(isinstance(sub_queries[index], dict), "Invalid sub_query")
        require(isinstance(outcome["id"], str) and outcome["id"] not in seen, "Invalid or duplicate outcome ID")
        seen.add(outcome["id"])
        require(outcome["id"] == sub_queries[index].get("id")
                and outcome["intent"] == sub_queries[index].get("intent"), "Outcome identity mismatch")
        require(isinstance(outcome["status"], str) and outcome["status"] in OUTCOME_STATUSES, "Unsupported outcome status")
        require(isinstance(outcome["missing"], list) and all(isinstance(item, str) for item in outcome["missing"]),
                "Invalid missing fields")
        require((outcome["status"] == "missing_args") == bool(outcome["missing"]), "Inconsistent missing_args")
        require(outcome["message"] is None or isinstance(outcome["message"], str), "Invalid outcome message")
        for field in ("calls", "referenced_articles"):
            require(isinstance(outcome[field], list), "Tool calls must be arrays")
            for call in outcome[field]:
                require(isinstance(call, dict) and set(call) == {"server", "tool", "args", "result", "error"},
                        "Unexpected ToolCall fields")
                require(isinstance(call["server"], str) and isinstance(call["tool"], str)
                        and isinstance(call["args"], dict), "Invalid ToolCall metadata")
                require(call["error"] is None or isinstance(call["error"], str), "Invalid ToolCall error")
                if sanitized and call["error"] is not None:
                    require(call["error"] == SAFE_TOOL_ERROR, "Tool diagnostic was not sanitized")
        if outcome["status"] in {"ok", "error"}:
            require(bool(outcome["calls"]), "Tool outcome requires a primary call")
        if sanitized and outcome["message"] is not None:
            require(outcome["message"] == SAFE_OUTCOME_MESSAGE, "Outcome diagnostic was not sanitized")
    require(not routing["halted"] or any(item["status"] == "empty" for item in outcomes),
            "Halted Routing requires an empty outcome")
    if sanitized and routing["halt_reason"] is not None:
        require(routing["halt_reason"] == SAFE_HALT_REASON, "Halt diagnostic was not sanitized")
    expected_error = None
    if decomposition["status"] == "failed":
        expected_status, expected_error = "FAILED", "DECOMPOSITION_FAILED"
    elif decomposition["status"] == "clarify":
        expected_status = "NEEDS_INPUT"
    elif any(item["status"] == "error" for item in outcomes):
        expected_status, expected_error = "FAILED", "ROUTING_FAILED"
    elif any(item["status"] == "missing_args" for item in outcomes):
        expected_status = "NEEDS_INPUT"
    else:
        expected_status = "COMPLETED"
    require(body["execution_status"] == expected_status, "Incorrect execution status mapping")
    if expected_error is None:
        require(body["error"] is None, "Non-failed execution has an error")
    else:
        error = body["error"]
        require(isinstance(error, dict) and set(error) == {"code", "message", "retryable"}, "Invalid execution error")
        require(error["code"] == expected_error and error["retryable"] is False, "Incorrect execution error mapping")
        require(isinstance(error["message"], str) and bool(error["message"].strip()), "Invalid execution error message")
        if sanitized:
            require(error["message"] == SAFE_EXECUTION_ERRORS[expected_error], "Execution diagnostic was not sanitized")


def inject_boundary_case(body: dict[str, Any], mode: str) -> dict[str, Any]:
    """Synthetic gateway-only cases. Never claim these came from real Routing."""
    value = copy.deepcopy(body)
    outcome = value["routing"]["outcomes"][0]
    if mode in {"general_knowledge", "out_of_scope"}:
        value["decomposition"]["sub_queries"][0].update({
            "intent": mode, "grounding": "none", "entities": {}, "inputs": [], "depends_on": []})
        outcome.update({"intent": mode, "status": mode, "calls": [], "referenced_articles": []})
    elif mode == "reference_error":
        outcome["referenced_articles"] = [{
            "server": "law-mcp", "tool": "get_article",
            "args": {"law": "subcontract_act", "article_no": "제13조"},
            "result": {"source": "stub", "unchanged_text": SYNTHETIC_DIAGNOSTIC},
            "error": SYNTHETIC_DIAGNOSTIC,
        }]
    elif mode == "diagnostics":
        outcome["status"] = "error"
        outcome["message"] = SYNTHETIC_DIAGNOSTIC
        outcome["calls"][0]["error"] = SYNTHETIC_DIAGNOSTIC
        outcome["calls"][0]["result"] = {"source": "stub", "unchanged_text": SYNTHETIC_DIAGNOSTIC}
        value["execution_status"] = "FAILED"
        value["error"] = {"code": "ROUTING_FAILED", "message": SYNTHETIC_DIAGNOSTIC, "retryable": False}
    else:
        raise AssertionError("Unknown synthetic boundary case")
    return value


def request(port: int, path: str, body: Any = None, *, key: str | None = None,
            raw: bytes | None = None, method: str = "POST", timeout: float = 8) -> tuple[int, Any]:
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["X-Internal-Api-Key"] = key
    data = raw if raw is not None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    if method == "GET":
        data = None
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        content = response.read()
        try:
            decoded = json.loads(content)
        except (ValueError, UnicodeDecodeError):
            raise AssertionError("Response is not JSON") from None
        require("application/json" in (response.getheader("Content-Type") or ""),
                "Response Content-Type is not JSON")
        return response.status, decoded
    finally:
        conn.close()


class Relay:
    """Observe requests in memory and forward them to the real Python API.

    Fault modes deliberately alter one boundary. They are reported separately
    from the unmodified end-to-end requests. Secret headers are never recorded.
    """

    def __init__(self, upstream: int, key: str):
        self.upstream = upstream
        self.key = key
        self.mode = "forward"
        self.records: list[dict[str, Any]] = []
        self.unexpected_paths: list[str] = []
        self.release_timeout = threading.Event()
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: Any) -> None:
                pass

            def do_GET(self) -> None:
                relay.unexpected_paths.append(self.path)
                self.respond(503, b'{"error":"external services disabled"}')

            def do_POST(self) -> None:
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if self.path != INTERNAL_PATH:
                    relay.unexpected_paths.append(self.path)
                    self.respond(503, b'{"error":"external services disabled"}')
                    return
                payload = json.loads(raw)
                relay.records.append({
                    "path": self.path,
                    "body": payload,
                    "authenticated": secrets.compare_digest(
                        self.headers.get("X-Internal-Api-Key", ""), relay.key),
                })
                mode = relay.mode
                if mode == "timeout":
                    relay.release_timeout.wait(10)
                    self.respond(504, b'{"error":"synthetic timeout"}')
                    return
                if mode == "invalid_json":
                    self.respond(200, b"not-json")
                    return
                if mode == "invalid_schema":
                    self.respond(200, b'{"request_id":"invalid"}')
                    return
                headers = {"Content-Type": "application/json"}
                if mode != "missing_auth":
                    headers["X-Internal-Api-Key"] = (
                        "deliberately-invalid" if mode == "wrong_auth" else
                        self.headers.get("X-Internal-Api-Key", "")
                    )
                conn = http.client.HTTPConnection("127.0.0.1", relay.upstream, timeout=8)
                try:
                    conn.request("POST", self.path, body=raw, headers=headers)
                    response = conn.getresponse()
                    result = response.read()
                    if mode == "wrong_id":
                        value = json.loads(result)
                        value["request_id"] = str(uuid.uuid4())
                        result = json.dumps(value).encode()
                    if mode in {"general_knowledge", "out_of_scope", "reference_error", "diagnostics"}:
                        require(response.status == 200, "Synthetic boundary case requires a successful stub response")
                        result = json.dumps(inject_boundary_case(json.loads(result), mode)).encode()
                    self.respond(response.status, result)
                except (OSError, http.client.HTTPException):
                    self.respond(503, json.dumps({
                        "request_id": payload.get("request_id"),
                        "error": {"code": "UPSTREAM_UNAVAILABLE",
                                  "message": "Test relay upstream is unavailable",
                                  "retryable": True},
                    }).encode())
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
        self.closed = False

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.release_timeout.set()
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)


class Process:
    def __init__(self, command: list[str], cwd: Path, env: dict[str, str], label: str):
        self.label = label
        self.closed = False
        self.log: list[str] = []
        self.process = subprocess.Popen(
            command, cwd=cwd, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, start_new_session=True,
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.log.append(line)

    def ready(self, port: int, timeout: float = 45) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            require(self.process.poll() is None, f"{self.label} exited before readiness")
            try:
                status, payload = request(port, "/health", method="GET", timeout=0.5)
                startup = "".join(self.log)
                own_startup = "Uvicorn running on" in startup or "Started NegoPilotApplication" in startup
                if status == 200 and payload == {"status": "ok"} and own_startup:
                    require(self.process.poll() is None, f"{self.label} exited during readiness")
                    return
            except (OSError, http.client.HTTPException, AssertionError):
                pass
            time.sleep(0.1)
        raise AssertionError(f"{self.label} did not become ready")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        # uv can create a separate child. This session and process group were
        # created exclusively for this command. Never look up processes by port.
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            pass
        # The leader may exit while a child ignores TERM or delays shutdown.
        # Clean up that owned group even when the leader has already exited.
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.process.wait(timeout=5)
        self.reader.join(timeout=2)
        if self.process.stdout is not None:
            self.process.stdout.close()


def unused_port(excluded: set[int]) -> int:
    for _ in range(30):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port not in excluded:
            excluded.add(port)
            return port
    raise RuntimeError("Unable to allocate a distinct local port")


def child_environment(key: str | None) -> dict[str, str]:
    # Do not inherit API credentials or application configuration from the shell.
    env = {name: value for name, value in os.environ.items() if name in {
        "PATH", "HOME", "JAVA_HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT",
        "UV_PYTHON_INSTALL_DIR", "UV_CACHE_DIR",
    }}
    env.update({
        "AGENT_EXECUTION_MODE": "stub",
        "AGENT_REQUEST_TIMEOUT_SECONDS": "5",
        "AGENT_CONNECT_TIMEOUT_SECONDS": "1",
        "AGENT_RESPONSE_TIMEOUT_SECONDS": "2",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": os.pathsep.join((str(ROOT / "agent/src"), str(ROOT / "shared/src"))),
        "UV_NO_SYNC": "1",
        "UV_OFFLINE": "1",
        "SERVER_ADDRESS": "127.0.0.1",
    })
    if key is not None:
        env["AGENT_INTERNAL_API_KEY"] = key
    return env


class Smoke:
    def __init__(self, jar: Path):
        self.jar = jar
        self.key = secrets.token_urlsafe(32)
        self.processes: list[Process] = []
        self.results: list[dict[str, str]] = []
        self.used_ports: set[int] = set()
        self.relay: Relay | None = None
        self.responses: list[Any] = []

    def check(self, name: str, fn: Any) -> None:
        try:
            fn()
        except Exception as error:
            message = str(error).replace(self.key, "[REDACTED]")
            self.results.append({"name": name, "status": "FAIL", "detail": message})
            print(f"FAIL {name}: {message}", flush=True)
        else:
            self.results.append({"name": name, "status": "PASS"})
            print(f"PASS {name}", flush=True)

    def start_python(self, key: str | None, label: str) -> int:
        port = unused_port(self.used_ports)
        env = child_environment(key)
        if self.relay is not None:
            for name in ("LAW_MCP_URL", "BUSINESS_MCP_URL", "OLLAMA_BASE_URL", "ANTHROPIC_BASE_URL"):
                env[name] = f"http://127.0.0.1:{self.relay.port}/disabled"
        process = Process([
            str(ROOT / "agent/.venv/bin/python"), "-B", "-m", "uvicorn",
            "negopilot_agent.api.app:app", "--host", "127.0.0.1", "--port", str(port),
            "--no-access-log",
        ], ROOT / "agent", env, label)
        self.processes.append(process)
        process.ready(port)
        return port

    def start_spring(self, key: str | None, label: str) -> int:
        assert self.relay is not None
        port = unused_port(self.used_ports)
        env = child_environment(key)
        env.update({"AGENT_BASE_URL": f"http://127.0.0.1:{self.relay.port}",
                    "SERVER_PORT": str(port)})
        process = Process([shutil.which("java") or "java", "-jar", str(self.jar)],
                          ROOT / "backend", env, label)
        self.processes.append(process)
        process.ready(port)
        return port

    def send(self, port: int, path: str, body: Any = None, **kwargs: Any) -> tuple[int, Any]:
        response = request(port, path, body, **kwargs)
        self.responses.append({"path": path, "method": kwargs.get("method", "POST").lower(),
                               "status": response[0], "body": response[1]})
        require(self.key not in json.dumps(response[1]), "Response exposed the internal key")
        return response

    def error(self, response: tuple[int, Any], status: int, code: str,
              request_id: str | None = None, retryable: bool = False,
              exact_id: bool = True) -> None:
        actual_status, body = response
        require(actual_status == status, f"Expected HTTP {status} but received {actual_status}")
        require(isinstance(body, dict), "Error body must be an object")
        require(set(body) == {"request_id", "error"}, "Unexpected error envelope")
        if exact_id:
            require(body["request_id"] == request_id, "Error request_id was not preserved")
        error = body["error"]
        require(isinstance(error, dict) and set(error) == {"code", "message", "retryable"},
                "Unexpected error fields")
        require(error["code"] == code, f"Expected error code {code}")
        require(error["retryable"] is retryable, "Incorrect retryable flag")
        require(isinstance(error["message"], str) and bool(error["message"].strip()),
                "Error message is missing")

    def execution(self, response: tuple[int, Any], fixture: dict[str, Any],
                  expected: str, request_id: str) -> None:
        status, body = response
        require(status == 200, f"Expected HTTP 200 but received {status}")
        validate_execution_payload(body)
        require(body["schema_version"] == "0.1", "Incorrect schema_version")
        require(body["request_id"] == request_id, "Execution request_id was not preserved")
        require(body["execution_status"] == expected, "Incorrect execution_status")
        require(body["generation_status"] == "NOT_IMPLEMENTED", "Generation status is misleading")
        require(body["mode"] == "stub", "Fixture decomposition is not labelled stub")
        require(matches_subset(body["decomposition"], fixture["expected"]),
                "Decomposition differs from the shared fixture")
        routing = body["routing"]
        require(routing["halted"] is False and routing["halt_reason"] is None,
                "Unexpected halt for the standard fixtures")
        if expected == "COMPLETED":
            require(routing["status"] == "routed", "Law fixture was not routed")
            require(len(routing["outcomes"]) == 1, "Expected one law outcome")
            outcome = routing["outcomes"][0]
            require(outcome["id"] == "q1" and outcome["intent"] == "law_article"
                    and outcome["status"] == "ok", "Incorrect law outcome")
            require(len(outcome["calls"]) == 1, "Expected one fake tool call")
            require(outcome["referenced_articles"] == [], "Unexpected law fixture reference lookup")
            call = outcome["calls"][0]
            require(call["server"] == "law-mcp" and call["tool"] == "get_article",
                    "Router selected an unexpected tool")
            require(call["args"] == {"law": "subcontract_act", "article_no": "제4조"},
                    "Router did not preserve fixture entities")
            require(call["error"] is None and has_stub_label(call["result"]),
                    "Fake tool result must be explicitly labelled stub")
        else:
            require(routing["status"] == "not_routed" and routing["outcomes"] == [],
                    "Non-ok decomposition must not call a tool")
        if expected == "FAILED":
            error = body["error"]
            require(error["code"] == "DECOMPOSITION_FAILED" and error["retryable"] is False,
                    "Incorrect failure mapping")
        else:
            require(body["error"] is None, "Non-failure result has an error")

    def run(self) -> bool:
        fixtures = [(json.loads((ROOT / "shared/fixtures/decomposition" / name).read_text()), status)
                    for name, status in FIXTURES]
        # Bind the relay first so outbound dependency URLs can point to its deny
        # handler even before Python starts. All real requests still target Python.
        self.relay = Relay(0, self.key)
        self.used_ports.add(self.relay.port)
        python = self.start_python(self.key, "Python stub")
        self.relay.upstream = python
        spring = self.start_spring(self.key, "Spring gateway")

        for label, port in (("python", python), ("spring", spring)):
            self.check(f"{label}.health", lambda port=port:
                       require(self.send(port, "/health", method="GET") == (200, {"status": "ok"}),
                               "Health contract mismatch"))

        history = [{"role": "user", "content": "테스트 이력 사용자 발화"},
                   {"role": "assistant", "content": "테스트 이력 응답"}]
        for fixture, expected in fixtures:
            request_id = str(uuid.uuid4())
            payload = {"request_id": request_id, "query": fixture["query"], "history": history}
            self.check(f"python.execution.{expected}", lambda payload=payload, fixture=fixture, expected=expected:
                       self.execution(self.send(python, INTERNAL_PATH, payload, key=self.key), fixture,
                                      expected, payload["request_id"]))
            self.check(f"e2e.{expected}", lambda payload=payload, fixture=fixture, expected=expected:
                       self.execution(self.send(spring, PUBLIC_PATH, payload), fixture,
                                      expected, payload["request_id"]))
            self.check(f"e2e.{expected}.history_and_identity", lambda payload=payload:
                       self.assert_forwarded(payload))

        fixture = fixtures[0][0]
        self.check("e2e.generated_request_id_and_default_history",
                   lambda: self.generated_id(spring, fixture))
        request_id = str(uuid.uuid4())
        valid = {"request_id": request_id, "query": fixture["query"]}

        invalid = {
            "blank_query": {**valid, "query": "  \t\n"},
            "long_query": {**valid, "query": "x" * 4001},
            "missing_query": {"request_id": request_id},
            "non_string_query": {**valid, "query": 3},
            "unknown_field": {**valid, "unexpected": True},
            "history_role": {**valid, "history": [{"role": "system", "content": "test"}]},
            "history_blank": {**valid, "history": [{"role": "user", "content": " "}]},
            "history_long": {**valid, "history": [{"role": "user", "content": "x" * 4001}]},
            "history_unknown_field": {**valid, "history": [{"role": "user", "content": "test", "x": 1}]},
            "history_too_many": {**valid, "history": history * 10 + history[:1]},
            "history_not_array": {**valid, "history": {}},
            "history_null": {**valid, "history": None},
            "unsupported_stub_query": {**valid, "query": "지원하지 않는 통합 테스트 질문"},
        }
        for label, port, path, key in (("python", python, INTERNAL_PATH, self.key),
                                        ("spring", spring, PUBLIC_PATH, None)):
            for case, payload in invalid.items():
                self.check(f"{label}.validation.{case}", lambda port=port, path=path, key=key, payload=payload:
                           self.error(self.send(port, path, payload, key=key), 400,
                                      "VALIDATION_ERROR", request_id))
            self.check(f"{label}.validation.invalid_uuid", lambda port=port, path=path, key=key:
                       self.error(self.send(port, path, {**valid, "request_id": "bad"}, key=key),
                                  400, "VALIDATION_ERROR"))
            self.check(f"{label}.validation.malformed_json", lambda port=port, path=path, key=key:
                       self.error(self.send(port, path, key=key, raw=b"{"), 400, "VALIDATION_ERROR"))

        self.check("python.required_request_id", lambda:
                   self.error(self.send(python, INTERNAL_PATH, {"query": fixture["query"]}, key=self.key),
                              400, "VALIDATION_ERROR"))
        for name, key in (("missing", None), ("wrong", "deliberately-invalid")):
            self.check(f"python.auth.{name}", lambda key=key:
                       self.error(self.send(python, INTERNAL_PATH, valid, key=key),
                                  401, "UNAUTHORIZED", request_id))

        self.check("e2e.history_maximum_boundary", lambda:
                   self.history_boundary(spring, fixture))
        for mode in ("general_knowledge", "out_of_scope", "reference_error", "diagnostics"):
            self.relay.mode = mode
            self.check(f"spring.synthetic_boundary.{mode}", lambda mode=mode:
                       self.synthetic_boundary(spring, valid, mode))
        for mode, code, status, retryable in (
            ("missing_auth", "UPSTREAM_INVALID_RESPONSE", 502, False),
            ("wrong_auth", "UPSTREAM_INVALID_RESPONSE", 502, False),
            ("wrong_id", "UPSTREAM_INVALID_RESPONSE", 502, False),
            ("invalid_json", "UPSTREAM_INVALID_RESPONSE", 502, False),
            ("invalid_schema", "UPSTREAM_INVALID_RESPONSE", 502, False),
            ("timeout", "UPSTREAM_TIMEOUT", 504, True),
        ):
            self.relay.mode = mode
            self.check(f"spring.boundary.{mode}", lambda code=code, status=status, retryable=retryable:
                       self.error(self.send(spring, PUBLIC_PATH, valid), status, code,
                                  request_id, retryable))
        self.relay.mode = "forward"
        self.relay.release_timeout.set()

        self.check("python.missing_server_key", lambda: self.missing_python_key(valid))
        self.check("spring.missing_server_key", lambda: self.missing_spring_key(valid))

        # Close only the relay owned by this run. The gateway now experiences an
        # actual refused upstream connection rather than a synthetic HTTP error.
        self.relay.close()
        self.check("spring.upstream_connection_refused", lambda:
                   self.error(self.send(spring, PUBLIC_PATH, valid), 503,
                              "UPSTREAM_UNAVAILABLE", request_id, True))
        self.check("no_model_or_mcp_requests", lambda:
                   require(not self.relay.unexpected_paths, "A disabled external dependency was called"))
        return all(result["status"] == "PASS" for result in self.results)

    def synthetic_boundary(self, spring: int, valid: dict[str, Any], mode: str) -> None:
        """Relay-injected compatibility checks are separate from true stub E2E."""
        assert self.relay is not None
        before = len(self.relay.records)
        status, body = self.send(spring, PUBLIC_PATH, valid)
        require(status == 200, "Synthetic boundary response was rejected")
        require(len(self.relay.records) == before + 1, "Synthetic boundary request was retried")
        validate_execution_payload(body)
        require(body["request_id"] == valid["request_id"] and body["mode"] == "stub", "Boundary identity changed")
        outcome = body["routing"]["outcomes"][0]
        if mode in {"general_knowledge", "out_of_scope"}:
            require(outcome["status"] == mode and body["execution_status"] == "COMPLETED",
                    "Tool-free statuses were merged or incorrectly mapped")
        elif mode == "reference_error":
            require(outcome["status"] == "ok" and body["execution_status"] == "COMPLETED",
                    "A referenced article failure changed primary execution status")
            reference = outcome["referenced_articles"][0]
            require(reference["error"] == SAFE_TOOL_ERROR, "Reference diagnostic was not sanitized")
            require(reference["result"] == {"source": "stub", "unchanged_text": SYNTHETIC_DIAGNOSTIC},
                    "Sanitization changed referenced result data")
        else:
            require(outcome["status"] == "error" and body["execution_status"] == "FAILED", "Incorrect routing failure mapping")
            require(outcome["calls"][0]["result"] == {"source": "stub", "unchanged_text": SYNTHETIC_DIAGNOSTIC},
                    "Sanitization changed primary result data")

    def assert_forwarded(self, expected: dict[str, Any]) -> None:
        assert self.relay is not None
        records = [r for r in self.relay.records if r["body"].get("request_id") == expected["request_id"]]
        require(len(records) == 1, "Request was not forwarded exactly once")
        record = records[0]
        require(record["body"] == expected, "Spring changed the request body or history")
        require(record["authenticated"], "Spring did not forward the internal key header")

    def generated_id(self, spring: int, fixture: dict[str, Any]) -> None:
        response = self.send(spring, PUBLIC_PATH, {"query": fixture["query"]})
        request_id = response[1].get("request_id")
        require(isinstance(request_id, str) and str(uuid.UUID(request_id)) == request_id,
                "Spring did not generate a canonical UUID")
        self.execution(response, fixture, "COMPLETED", request_id)
        self.assert_forwarded({"request_id": request_id, "query": fixture["query"], "history": []})

    def history_boundary(self, spring: int, fixture: dict[str, Any]) -> None:
        payload = {"request_id": str(uuid.uuid4()), "query": fixture["query"],
                   "history": [{"role": "user" if i % 2 == 0 else "assistant",
                                "content": "x" * 4000} for i in range(20)]}
        self.execution(self.send(spring, PUBLIC_PATH, payload), fixture, "COMPLETED", payload["request_id"])
        self.assert_forwarded(payload)

    def missing_python_key(self, valid: dict[str, Any]) -> None:
        port = self.start_python(None, "Python without configured key")
        response = self.send(port, INTERNAL_PATH, valid, key=self.key)
        # The contract requires this configuration to fail closed but does not
        # assign a distinct status code to an unset server-side key.
        require(response[0] in (401, 503), "Python without a key did not fail closed")
        code = "UNAUTHORIZED" if response[0] == 401 else "UPSTREAM_UNAVAILABLE"
        self.error(response, response[0], code, valid["request_id"], response[0] == 503)

    def missing_spring_key(self, valid: dict[str, Any]) -> None:
        port = self.start_spring(None, "Spring without configured key")
        response = self.send(port, PUBLIC_PATH, valid)
        require(response[0] in (502, 503), "Spring without a key did not fail closed")
        code = "UPSTREAM_INVALID_RESPONSE" if response[0] == 502 else "UPSTREAM_UNAVAILABLE"
        self.error(response, response[0], code, valid["request_id"], response[0] == 503)

    def close(self) -> None:
        for process in reversed(self.processes):
            process.close()
        if self.relay is not None:
            self.relay.close()

    def check_logs(self) -> None:
        self.check("logs.no_internal_key", lambda:
                   require(all(self.key not in "".join(p.log) for p in self.processes),
                           "A child process logged the internal key"))
        self.check("logs.no_test_history", lambda:
                   require(all("테스트 이력 사용자 발화" not in "".join(p.log) for p in self.processes),
                           "A child process logged request history"))

    def check_openapi(self) -> None:
        result = subprocess.run(
            [str(ROOT / "agent/.venv/bin/python"), "-B",
             str(ROOT / "scripts/integration/check_openapi.py")],
            input=json.dumps(self.responses), text=True, capture_output=True,
            cwd=ROOT, env=child_environment(None), timeout=15,
        )
        require(result.returncode == 0, "OpenAPI validation failed: " + result.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jar", type=Path, help="Already built Spring executable JAR")
    parser.add_argument("--report", type=Path,
                        default=Path("/tmp") / f"negopilot-http-smoke-{uuid.uuid4().hex}.json",
                        help="Summary JSON without keys or request bodies. Defaults to a unique file in /tmp")
    args = parser.parse_args()
    jars = [path for path in (ROOT / "backend/target").glob("*.jar") if not path.name.endswith(".original")]
    jar = args.jar.resolve() if args.jar else (jars[0] if len(jars) == 1 else None)
    if not jar or not jar.is_file():
        print("Prepare the Spring executable JAR first or select it with --jar.", file=sys.stderr)
        return 2
    if not (ROOT / "agent/.venv/bin/python").is_file():
        print("Prepare agent/.venv/bin/python and its application dependencies before running this script.", file=sys.stderr)
        return 2
    smoke = Smoke(jar)
    started = time.monotonic()
    interrupted = False
    try:
        smoke.run()
    except KeyboardInterrupt:
        interrupted = True
    except Exception as error:
        smoke.results.append({"name": "runner", "status": "FAIL",
                              "detail": f"{type(error).__name__}: {str(error).replace(smoke.key, '[REDACTED]')}"})
        print("FAIL runner: " + smoke.results[-1]["detail"], flush=True)
    finally:
        smoke.close()
    if interrupted:
        print("Cancelled. All owned processes stopped.", flush=True)
        return 130
    smoke.check_logs()
    if smoke.responses:
        smoke.check("responses.match_openapi", smoke.check_openapi)
    passed = sum(result["status"] == "PASS" for result in smoke.results)
    report = {"mode": "stub", "duration_seconds": round(time.monotonic() - started, 2),
              "passed": passed, "total": len(smoke.results), "checks": smoke.results,
              "limitations": ["No live LLM or MCP calls", "No DB seed or mail",
                              "History forwarding is observed at the HTTP boundary",
                              "Synthetic relay checks are not real Python Router executions",
                              "Port allocation has a brief bind-to-start race"]}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"Summary report: {args.report.resolve()}")
    print(f"Result: {passed}/{len(smoke.results)} checks passed. All owned processes stopped.")
    return 0 if passed == len(smoke.results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
