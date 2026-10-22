#!/usr/bin/env python3
"""Shared process, HTTP and database helpers for the Spring/Python smoke runners.

Spring runs with its default profile: membership authentication, CSRF and an
application PostgreSQL DB. Each run starts one disposable PostgreSQL container,
creates an approved member with the JAR's bootstrap CLIs and signs in through
the public API. Only processes and containers started by the run are stopped.
"""

from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import threading
import time
import uuid
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
POSTGRES_IMAGE = "postgres:16"
MEMBER_EMAIL = "smoke-user@negopilot.local"
ADMIN_EMAIL = "smoke-admin@negopilot.local"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def exchange(port: int, path: str, body: Any = None, *, method: str = "POST",
             headers: dict[str, str] | None = None, raw: bytes | None = None,
             timeout: float = 8) -> tuple[int, Any, http.client.HTTPMessage]:
    request_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    request_headers.update(headers or {})
    data = raw if raw is not None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    if method in {"GET", "DELETE"} and raw is None and body is None:
        data = None
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request(method, path, body=data, headers=request_headers)
        response = conn.getresponse()
        content = response.read()
        if response.status == 204 and not content:
            return response.status, None, response.headers
        try:
            decoded = json.loads(content)
        except (ValueError, UnicodeDecodeError):
            raise AssertionError("Response is not JSON") from None
        require("application/json" in (response.getheader("Content-Type") or ""),
                "Response Content-Type is not JSON")
        return response.status, decoded, response.headers
    finally:
        conn.close()


def request(port: int, path: str, body: Any = None, *, key: str | None = None,
            raw: bytes | None = None, method: str = "POST", timeout: float = 8) -> tuple[int, Any]:
    headers = {} if key is None else {"X-Internal-Api-Key": key}
    status, decoded, _ = exchange(port, path, body, method=method, headers=headers, raw=raw, timeout=timeout)
    return status, decoded


class SpringSession:
    """Cookie and CSRF state of one signed-in member. Never prints tokens or cookies."""

    def __init__(self, port: int):
        self.port = port
        self.cookies: dict[str, str] = {}
        self.csrf: dict[str, str] = {}

    def send(self, method: str, path: str, body: Any = None, *, csrf: bool = True,
             headers: dict[str, str] | None = None, timeout: float = 10) -> tuple[int, Any]:
        request_headers = dict(headers or {})
        if self.cookies:
            request_headers["Cookie"] = "; ".join(f"{name}={value}" for name, value in self.cookies.items())
        if csrf and method not in {"GET", "HEAD"}:
            request_headers.update(self.csrf)
        status, decoded, response_headers = exchange(self.port, path, body, method=method,
                                                     headers=request_headers, timeout=timeout)
        for cookie in response_headers.get_all("Set-Cookie") or []:
            pair, _, attributes = cookie.partition(";")
            name, _, value = pair.strip().partition("=")
            if not value or "max-age=0" in attributes.lower().replace(" ", ""):
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value
        return status, decoded

    def refresh_csrf(self) -> None:
        status, token = self.send("GET", "/api/auth/csrf")
        require(status == 200 and isinstance(token, dict) and isinstance(token.get("header_name"), str)
                and bool(token.get("token")), "CSRF token response is incomplete")
        self.csrf = {token["header_name"]: token["token"]}

    def login(self, email: str, password: str) -> None:
        self.refresh_csrf()
        status, body = self.send("POST", "/api/auth/login", {"email": email, "password": password})
        require(status == 200 and body["user"]["status"] == "ACTIVE", "Smoke member could not sign in")
        self.refresh_csrf()  # Login rotates the session and invalidates the earlier token.


class PostgresContainer:
    """Disposable application DB. Only the container named by this run is removed."""

    def __init__(self) -> None:
        self.name = f"negopilot-smoke-{uuid.uuid4().hex[:12]}"
        self.password = secrets.token_urlsafe(18)
        self.port = 0
        self.started = False
        self.docker = shutil.which("docker")

    def start(self, timeout: float = 90) -> None:
        require(self.docker is not None, "Docker is required to start the disposable PostgreSQL DB")
        env = os.environ | {"POSTGRES_PASSWORD": self.password}
        result = subprocess.run(
            [self.docker, "run", "-d", "--rm", "--name", self.name, "-e", "POSTGRES_USER=smoke",
             "-e", "POSTGRES_PASSWORD", "-e", "POSTGRES_DB=negopilot_app", "-p", "127.0.0.1::5432", POSTGRES_IMAGE],
            env=env, capture_output=True, text=True, timeout=300)
        require(result.returncode == 0, "Disposable PostgreSQL container did not start")
        self.started = True
        mapped = subprocess.run([self.docker, "port", self.name, "5432/tcp"],
                                capture_output=True, text=True, timeout=30).stdout.split()
        require(bool(mapped), "PostgreSQL port was not published")
        self.port = int(mapped[0].rsplit(":", 1)[1])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # -h uses TCP, so the temporary socket-only init server does not count as ready.
            ready = subprocess.run([self.docker, "exec", self.name, "pg_isready", "-h", "127.0.0.1",
                                    "-U", "smoke", "-d", "negopilot_app"], capture_output=True, timeout=30)
            if ready.returncode == 0:
                return
            time.sleep(0.5)
        raise AssertionError("Disposable PostgreSQL DB did not become ready")

    def environment(self) -> dict[str, str]:
        return {"APP_DATABASE_URL": f"jdbc:postgresql://127.0.0.1:{self.port}/negopilot_app",
                "APP_DATABASE_USER": "smoke", "APP_DATABASE_PASSWORD": self.password}

    def close(self) -> None:
        if self.started:
            self.started = False
            subprocess.run([self.docker, "rm", "-f", self.name], capture_output=True, timeout=60)


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

    def ready(self, port: int, timeout: float = 60) -> None:
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

    def wait(self, timeout: float = 120) -> int:
        try:
            code = self.process.wait(timeout=timeout)
        finally:
            self.reader.join(timeout=2)
        return code

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


def member_password() -> str:
    # Membership requires a letter, a digit and a symbol.
    return "Smoke-" + secrets.token_urlsafe(18) + "7!"


class Smoke:
    def __init__(self, jar: Path):
        self.jar = jar
        self.key = secrets.token_urlsafe(32)
        self.processes: list[Process] = []
        self.results: list[dict[str, str]] = []
        self.used_ports: set[int] = set()
        self.relay: Any = None
        self.database: PostgresContainer | None = None
        self.member_password = member_password()
        self.responses: list[Any] = []
        self.cleanup_started = False

    def redact(self, text: str) -> str:
        return text.replace(self.key, "[REDACTED]").replace(self.member_password, "[REDACTED]")

    def check(self, name: str, fn: Any) -> None:
        try:
            fn()
        except Exception as error:
            message = self.redact(str(error))
            self.results.append({"name": name, "status": "FAIL", "detail": message})
            print(f"FAIL {name}: {message}", flush=True)
        else:
            self.results.append({"name": name, "status": "PASS"})
            print(f"PASS {name}", flush=True)

    def start_python(self, key: str | None, label: str) -> int:
        port = unused_port(self.used_ports)
        env = child_environment(key)
        if self.relay is not None:
            for name in ("LAW_MCP_URL", "BUSINESS_MCP_URL", "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL"):
                env[name] = f"http://127.0.0.1:{self.relay.port}/disabled"
        process = Process([
            str(ROOT / "agent/.venv/bin/python"), "-B", "-m", "uvicorn",
            "negopilot_agent.api.app:app", "--host", "127.0.0.1", "--port", str(port),
            "--no-access-log",
        ], ROOT / "agent", env, label)
        self.processes.append(process)
        process.ready(port)
        return port

    def spring_environment(self, key: str | None) -> dict[str, str]:
        assert self.database is not None
        env = child_environment(key)
        env.update(self.database.environment())
        # The owned server is plain HTTP on loopback, so the session cookie cannot be Secure-only.
        env["SESSION_COOKIE_SECURE"] = "false"
        return env

    def bootstrap(self, main_class: str, arguments: list[str], extra: dict[str, str], label: str) -> None:
        env = self.spring_environment(None) | extra
        process = Process([shutil.which("java") or "java", f"-Dloader.main={main_class}", "-cp", str(self.jar),
                           "org.springframework.boot.loader.launch.PropertiesLauncher", *arguments],
                          ROOT / "backend", env, label)
        self.processes.append(process)
        require(process.wait() == 0, f"{label} failed")

    def start_database(self) -> None:
        self.database = PostgresContainer()
        self.database.start()
        # The member CLI requires an active administrator before it creates the approved USER.
        self.bootstrap("com.negopilot.backend.AuthBootstrapAdmin",
                       ["--if-absent", f"--email={ADMIN_EMAIL}", "--name=스모크 관리자", "--department=개발팀"],
                       {"ADMIN_BOOTSTRAP_PASSWORD": member_password()}, "Spring admin bootstrap")
        self.bootstrap("com.negopilot.backend.AuthBootstrapLocalUser", [],
                       {"LOCAL_USER_EMAIL": MEMBER_EMAIL, "LOCAL_USER_PASSWORD": self.member_password},
                       "Spring member bootstrap")

    def start_spring(self, key: str | None, label: str) -> int:
        assert self.relay is not None
        if self.database is None:
            self.start_database()
        port = unused_port(self.used_ports)
        env = self.spring_environment(key)
        env.update({"AGENT_BASE_URL": f"http://127.0.0.1:{self.relay.port}", "SERVER_PORT": str(port)})
        process = Process([shutil.which("java") or "java", "-jar", str(self.jar)],
                          ROOT / "backend", env, label)
        self.processes.append(process)
        process.ready(port)
        return port

    def sign_in(self, port: int) -> SpringSession:
        session = SpringSession(port)
        session.login(MEMBER_EMAIL, self.member_password)
        return session

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

    def close(self) -> None:
        if self.cleanup_started:
            return
        self.cleanup_started = True
        # One failed cleanup must not skip the other owned processes, the relay or the DB.
        owned = [(process.label, process.close) for process in reversed(self.processes)]
        if self.relay is not None:
            owned.append(("relay", self.relay.close))
        if self.database is not None:
            owned.append(("database", self.database.close))
        for label, close in owned:
            try:
                close()
            except Exception as error:
                self.results.append({"name": "cleanup." + label, "status": "FAIL",
                                     "detail": f"{type(error).__name__}: {self.redact(str(error))}"})
