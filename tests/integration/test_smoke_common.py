"""Cost-free checks of the shared smoke helpers. Execute with the standard library unittest runner."""

import importlib.util
import os
from pathlib import Path
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/integration"))
SPEC = importlib.util.spec_from_file_location("smoke_common", ROOT / "scripts/integration/smoke_common.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def headers(*cookies):
    message = Mock()
    message.get_all.return_value = list(cookies)
    return message


class SmokeCommonTest(unittest.TestCase):
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

    def test_spring_uses_default_profile_with_owned_database_only(self):
        runner = smoke.Smoke(Path("unused.jar"))
        runner.relay = SimpleNamespace(port=12345)
        runner.database = SimpleNamespace(environment=lambda: {
            "APP_DATABASE_URL": "jdbc:postgresql://127.0.0.1:5555/negopilot_app",
            "APP_DATABASE_USER": "smoke", "APP_DATABASE_PASSWORD": "owned-secret"})
        inherited = {"SPRING_PROFILES_ACTIVE": "legacy-test",
                     "SPRING_APPLICATION_JSON": '{"spring":{"profiles":{"active":"production"}}}',
                     "APP_DATABASE_URL": "jdbc:postgresql://unused/application",
                     "APP_DATABASE_PASSWORD": "inherited-secret",
                     "BUSINESS_DATABASE_ENABLED": "true"}
        with patch.dict(os.environ, inherited), patch.object(smoke, "Process") as process_class, \
                patch.object(smoke, "unused_port", return_value=23456):
            self.assertEqual(runner.start_spring("ephemeral-key", "mock Spring"), 23456)
        command, cwd, env, label = process_class.call_args.args
        self.assertEqual(command[1:], ["-jar", "unused.jar"])
        self.assertNotIn("SPRING_PROFILES_ACTIVE", env)
        self.assertNotIn("SPRING_APPLICATION_JSON", env)
        self.assertNotIn("BUSINESS_DATABASE_ENABLED", env)
        self.assertEqual(env["APP_DATABASE_URL"], "jdbc:postgresql://127.0.0.1:5555/negopilot_app")
        self.assertEqual(env["APP_DATABASE_PASSWORD"], "owned-secret")
        self.assertEqual(env["SESSION_COOKIE_SECURE"], "false")
        self.assertEqual(env["AGENT_EXECUTION_MODE"], "stub")
        self.assertEqual(env["AGENT_BASE_URL"], "http://127.0.0.1:12345")
        self.assertEqual(env["SERVER_ADDRESS"], "127.0.0.1")
        self.assertEqual(env["SERVER_PORT"], "23456")
        process_class.return_value.ready.assert_called_once_with(23456)

    def test_database_bootstrap_creates_admin_before_member(self):
        runner = smoke.Smoke(Path("unused.jar"))
        database = Mock()
        database.environment.return_value = {"APP_DATABASE_URL": "jdbc:postgresql://127.0.0.1:1/app"}
        with patch.object(smoke, "PostgresContainer", return_value=database), \
                patch.object(smoke, "Process") as process_class:
            process_class.return_value.wait.return_value = 0
            runner.start_database()
        database.start.assert_called_once_with()
        mains = [call.args[0][1] for call in process_class.call_args_list]
        self.assertEqual(mains, ["-Dloader.main=com.negopilot.backend.AuthBootstrapAdmin",
                                 "-Dloader.main=com.negopilot.backend.AuthBootstrapLocalUser"])
        member_env = process_class.call_args_list[1].args[2]
        self.assertEqual(member_env["LOCAL_USER_EMAIL"], smoke.MEMBER_EMAIL)
        self.assertEqual(member_env["LOCAL_USER_PASSWORD"], runner.member_password)

    def test_member_password_meets_membership_rules(self):
        password = smoke.member_password()
        self.assertGreaterEqual(len(password), 8)
        self.assertLessEqual(len(password.encode()), 72)
        self.assertTrue(any(c.isalpha() for c in password) and any(c.isdigit() for c in password)
                        and any(not c.isalnum() for c in password))

    def test_session_follows_login_rotation_and_sends_csrf_only_on_changes(self):
        session = smoke.SpringSession(8080)
        calls = []

        def fake_exchange(port, path, body=None, *, method, headers: dict, timeout):
            calls.append((method, path, dict(headers)))
            if path == "/api/auth/csrf":
                issued = headers_of() if "Cookie" in headers else headers_of("SESSION=first; Path=/; HttpOnly")
                return 200, {"header_name": "X-CSRF-TOKEN", "token": f"token-{len(calls)}"}, issued
            if path == "/api/auth/login":
                return 200, {"user": {"status": "ACTIVE"}}, headers_of("SESSION=rotated; Path=/; HttpOnly")
            return 200, {"items": []}, headers_of()

        headers_of = headers
        with patch.object(smoke, "exchange", side_effect=fake_exchange):
            session.login("member@example.test", "password")
            session.send("GET", "/api/conversations")
            session.send("POST", "/api/conversations", {"context": {"selected_supplier_ids": []}})
        login = next(call for call in calls if call[1] == "/api/auth/login")
        self.assertEqual(login[2]["X-CSRF-TOKEN"], "token-1")
        self.assertEqual(login[2]["Cookie"], "SESSION=first")
        self.assertEqual(session.cookies, {"SESSION": "rotated"})
        self.assertEqual(session.csrf, {"X-CSRF-TOKEN": "token-3"})
        self.assertNotIn("X-CSRF-TOKEN", calls[-2][2])
        self.assertEqual(calls[-2][2]["Cookie"], "SESSION=rotated")
        self.assertEqual(calls[-1][2]["X-CSRF-TOKEN"], "token-3")

    def test_session_drops_expired_cookie(self):
        session = smoke.SpringSession(8080)
        session.cookies = {"SESSION": "old"}
        with patch.object(smoke, "exchange", return_value=(204, None, headers("SESSION=; Max-Age=0; Path=/"))):
            session.send("POST", "/api/auth/logout")
        self.assertEqual(session.cookies, {})

    def test_database_close_removes_only_its_own_container_once(self):
        database = smoke.PostgresContainer()
        database.docker = "/usr/bin/docker"
        database.started = True
        with patch.object(smoke.subprocess, "run") as run:
            database.close()
            database.close()
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["/usr/bin/docker", "rm", "-f", database.name])
        self.assertTrue(database.name.startswith("negopilot-smoke-"))

    def test_database_requires_docker(self):
        database = smoke.PostgresContainer()
        database.docker = None
        with self.assertRaises(AssertionError):
            database.start()
        self.assertFalse(database.started)

    def test_error_checker_detects_lost_id_and_wrong_retry_semantics(self):
        runner = smoke.Smoke(Path("unused.jar"))
        payload = {"request_id": "expected", "error": {
            "code": "UPSTREAM_TIMEOUT", "message": "Timed out", "retryable": True}}
        runner.error((504, payload), 504, "UPSTREAM_TIMEOUT", "expected", True)
        with self.assertRaises(AssertionError):
            runner.error((504, payload), 504, "UPSTREAM_TIMEOUT", "different", True)
        with self.assertRaises(AssertionError):
            runner.error((504, payload), 504, "UPSTREAM_TIMEOUT", "expected", False)

    def test_cleanup_closes_database_after_one_process_failure_and_redacts_secrets(self):
        runner = smoke.Smoke(Path("unused.jar"))
        failing = Mock(label="spring")
        failing.close.side_effect = RuntimeError("leak " + runner.key + " " + runner.member_password)
        runner.processes = [failing]
        runner.relay = Mock()
        runner.database = Mock()
        runner.close()
        runner.close()
        runner.relay.close.assert_called_once_with()
        runner.database.close.assert_called_once_with()
        self.assertEqual([item["name"] for item in runner.results], ["cleanup.spring"])
        self.assertNotIn(runner.key, runner.results[0]["detail"])
        self.assertNotIn(runner.member_password, runner.results[0]["detail"])

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
