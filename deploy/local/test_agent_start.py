"""Offline Docker entrypoint tests. No API keys from the developer environment are read."""

import importlib.util
import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("docker_agent_start", Path(__file__).with_name("agent_start.py"))
startup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(startup)
BASE = {"AGENT_INTERNAL_API_KEY": "unit-internal-key"}


class AgentStartTests(unittest.TestCase):
    def setUp(self):
        self.network_guard = patch("socket.socket.connect", side_effect=AssertionError("Network forbidden"))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)

    def test_default_live_requires_openai_key_before_launch(self):
        with patch.dict(startup.os.environ, BASE, clear=True), patch.object(startup.os, "execvpe") as execute:
            output = io.StringIO()
            with redirect_stderr(output):
                result = startup.main(["uvicorn", "negopilot_agent.api.app:app"])
        self.assertEqual(result, 2)
        self.assertIn("OPENAI_API_KEY", output.getvalue())
        execute.assert_not_called()

    def test_openai_defaults_and_empty_model_are_normalized_before_exec(self):
        env = BASE | {"OPENAI_API_KEY": "unit-cloud-key", "NEGOPILOT_GENERATOR_MODEL": ""}
        with patch.dict(startup.os.environ, env, clear=True), patch.object(startup.os, "execvpe") as execute:
            self.assertEqual(startup.main(["uvicorn", "app:app"]), 0)
        command, args, actual = execute.call_args.args
        self.assertEqual((command, args), ("uvicorn", ["uvicorn", "app:app"]))
        self.assertEqual(actual["AGENT_EXECUTION_MODE"], "live")
        for phase in ("DECOMPOSER", "GENERATOR"):
            self.assertEqual(actual[f"NEGOPILOT_{phase}_BACKEND"], "openai")
            self.assertEqual(actual[f"NEGOPILOT_{phase}_MODEL"], "gpt-4.1-mini")

    def test_stub_ignores_missing_keys_and_unused_invalid_provider_settings(self):
        env = BASE | {"AGENT_EXECUTION_MODE": "stub", "NEGOPILOT_DECOMPOSER_BACKEND": "unused",
                      "NEGOPILOT_GENERATOR_MODEL": " ", "OLLAMA_BASE_URL": "not-a-url"}
        self.assertEqual(startup.prepare_environment(env), env)

    def test_each_selected_provider_is_required_for_mixed_phases(self):
        env = BASE | {"NEGOPILOT_GENERATOR_BACKEND": "anthropic", "OPENAI_API_KEY": "unit-openai"}
        with self.assertRaisesRegex(startup.ConfigurationError, "ANTHROPIC_API_KEY"):
            startup.prepare_environment(env)
        result = startup.prepare_environment(env | {"ANTHROPIC_API_KEY": "unit-anthropic"})
        from negopilot_agent.generation.generator import DEFAULT_MODELS
        self.assertEqual(result["NEGOPILOT_GENERATOR_MODEL"], DEFAULT_MODELS["anthropic"])
        self.assertEqual(result["NEGOPILOT_DECOMPOSER_MODEL"], "gpt-4.1-mini")

    def test_explicit_models_are_preserved(self):
        env = BASE | {"OPENAI_API_KEY": "unit-openai", "NEGOPILOT_DECOMPOSER_MODEL": "my-model-version",
                      "NEGOPILOT_GENERATOR_MODEL": "another-model"}
        result = startup.prepare_environment(env)
        self.assertEqual(result["NEGOPILOT_DECOMPOSER_MODEL"], "my-model-version")
        self.assertEqual(result["NEGOPILOT_GENERATOR_MODEL"], "another-model")
        self.assertEqual(env["NEGOPILOT_DECOMPOSER_MODEL"], "my-model-version")

    def test_invalid_selected_provider_model_and_key_fail_without_echoing_values(self):
        env = BASE | {"OPENAI_API_KEY": "unit-openai"}
        for changes, field in [({"NEGOPILOT_DECOMPOSER_BACKEND": "invalid-secret"}, "BACKEND"),
                               ({"NEGOPILOT_GENERATOR_BACKEND": ""}, "BACKEND"),
                               ({"NEGOPILOT_DECOMPOSER_MODEL": " "}, "MODEL"),
                               ({"NEGOPILOT_GENERATOR_MODEL": "invalid-secret\n"}, "MODEL"),
                               ({"OPENAI_API_KEY": "invalid-secret\n"}, "OPENAI_API_KEY")]:
            with self.subTest(field=field), self.assertRaises(startup.ConfigurationError) as failure:
                startup.prepare_environment(env | changes)
            self.assertIn(field, str(failure.exception))
            self.assertNotIn("invalid-secret", str(failure.exception))

    def test_anthropic_effort_and_fallback_validation_only_when_used(self):
        env = BASE | {"ANTHROPIC_API_KEY": "unit-anthropic", "NEGOPILOT_DECOMPOSER_BACKEND": "anthropic",
                      "NEGOPILOT_GENERATOR_BACKEND": "anthropic", "NEGOPILOT_GENERATOR_EFFORT": "xhigh",
                      "NEGOPILOT_GENERATOR_FALLBACKS": "0"}
        startup.prepare_environment(env)
        for name in ("NEGOPILOT_DECOMPOSER_EFFORT", "NEGOPILOT_GENERATOR_FALLBACKS"):
            with self.subTest(name=name), self.assertRaisesRegex(startup.ConfigurationError, name):
                startup.prepare_environment(env | {name: "unsupported"})
        startup.prepare_environment(BASE | {"OPENAI_API_KEY": "unit-openai", "NEGOPILOT_GENERATOR_EFFORT": "unused"})

    def test_ollama_needs_no_cloud_key_and_uses_provider_defaults(self):
        env = BASE | {f"NEGOPILOT_{phase}_BACKEND": "ollama" for phase in ("DECOMPOSER", "GENERATOR")}
        result = startup.prepare_environment(env)
        self.assertEqual(result["OLLAMA_BASE_URL"], "http://host.docker.internal:11434")
        self.assertEqual(result["NEGOPILOT_DECOMPOSER_MODEL"], "qwen3:8b")
        self.assertEqual(result["NEGOPILOT_GENERATOR_MODEL"], "qwen3:8b")

    def test_ollama_invalid_url_timeouts_and_token_limits_fail_offline(self):
        env = BASE | {f"NEGOPILOT_{phase}_BACKEND": "ollama" for phase in ("DECOMPOSER", "GENERATOR")}
        invalid = [("OLLAMA_BASE_URL", value) for value in (
            "http://user:invalid-secret@host:11434", "http://host/api/chat", "http://host:invalid",
            "http://host?key=invalid-secret", "file:///tmp/model", "http://bad host")]
        invalid += [("OLLAMA_TIMEOUT_SECONDS", "nan"), ("OLLAMA_TIMEOUT_SECONDS", "3601"),
                    ("OLLAMA_NUM_CTX", "1023"), ("OLLAMA_NUM_PREDICT", "32768"), ("OLLAMA_NUM_PREDICT", "zero")]
        for name, value in invalid:
            with self.subTest(name=name, value=value), self.assertRaises(startup.ConfigurationError) as failure:
                startup.prepare_environment(env | {name: value})
            self.assertNotIn("invalid-secret", str(failure.exception))

    def test_common_api_settings_are_checked_in_stub_too(self):
        env = BASE | {"AGENT_EXECUTION_MODE": "stub"}
        for changes in ({"AGENT_EXECUTION_MODE": "unknown"}, {"AGENT_INTERNAL_API_KEY": ""},
                        {"AGENT_REQUEST_TIMEOUT_SECONDS": "nan"}, {"AGENT_REQUEST_TIMEOUT_SECONDS": "0"}):
            with self.subTest(changes=changes), self.assertRaises(startup.ConfigurationError):
                startup.prepare_environment(env | changes)


if __name__ == "__main__":
    unittest.main()
