"""Render Compose contracts without contacting Docker Engine or model providers.

Run explicitly in the Docker CI job. The name deliberately does not match the
Docker-free helper suite's test_*.py discovery pattern.
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
BASE = ("compose.yaml",)
TEST = (*BASE, "compose.test.yaml")
PROVIDER_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")


def render(files, *, variables=None, dotenv=""):
    # Never interpolate the developer's real credentials or local .env file.
    environment = {name: os.environ[name] for name in ("PATH", "HOME", "DOCKER_CONFIG") if name in os.environ}
    environment.update(variables or {})
    with tempfile.TemporaryDirectory(prefix="negopilot-compose-config-") as directory:
        env_file = Path(directory) / ".env"
        env_file.write_text(dotenv, encoding="utf-8")
        command = ["docker", "compose", "--env-file", str(env_file)]
        for file in files:
            command.extend(("-f", str(ROOT / file)))
        command.extend(("config", "--format", "json"))
        result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30)
    if result.returncode:
        # Input contains only synthetic values. Do not dump the rendered environment.
        raise AssertionError(f"Compose config failed with exit {result.returncode}: {result.stderr.strip()}")
    return json.loads(result.stdout)


def volume_names(config):
    return {value["name"] for value in config["volumes"].values()}


class ComposeConfigurationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.live = render(BASE)
        cls.test = render(TEST)

    def assert_modes(self, config, expected):
        for service in ("agent", "backend"):
            with self.subTest(service=service):
                self.assertEqual(config["services"][service]["environment"]["AGENT_EXECUTION_MODE"], expected)

    def assert_port(self, config, expected):
        self.assertEqual(config["services"]["frontend"]["ports"], [{
            "mode": "ingress", "host_ip": "127.0.0.1", "target": 80,
            "published": str(expected), "protocol": "tcp",
        }])

    def test_base_is_live_and_config_can_render_before_provider_credentials_are_set(self):
        self.assert_modes(self.live, "live")
        agent = self.live["services"]["agent"]["environment"]
        backend = self.live["services"]["backend"]["environment"]
        self.assertEqual(agent["AGENT_REQUEST_TIMEOUT_SECONDS"], "300")
        self.assertEqual(backend["AGENT_RESPONSE_TIMEOUT_SECONDS"], "310")
        for key in PROVIDER_KEYS:
            self.assertEqual(agent.get(key), "")
        self.assert_port(self.live, 5173)

    def test_test_overlay_is_stub_and_replaces_the_live_published_port(self):
        self.assertEqual(self.test["name"], "negopilot-test")
        self.assert_modes(self.test, "stub")
        self.assert_port(self.test, 5174)

    def test_test_session_cookie_is_separate_from_the_unchanged_live_default(self):
        cookie_setting = "SERVER_SERVLET_SESSION_COOKIE_NAME"
        self.assertNotIn(cookie_setting, self.live["services"]["backend"]["environment"])
        self.assertEqual(self.test["services"]["backend"]["environment"][cookie_setting],
                         "NEGOPILOT_TEST_SESSION")

    def test_test_volumes_are_not_live_volumes(self):
        self.assertEqual(set(self.test["volumes"]), set(self.live["volumes"]))
        self.assertTrue(volume_names(self.live).isdisjoint(volume_names(self.test)))
        for service, source in (("postgres", "postgres-data"),):
            mounts = self.test["services"][service]["volumes"]
            self.assertTrue(any(mount["type"] == "volume" and mount["source"] == source for mount in mounts))

    def test_test_overlay_discards_provider_credentials_from_a_live_dotenv(self):
        config = render(TEST, dotenv="\n".join((
            "OPENAI_API_KEY=synthetic-openai-value",
            "ANTHROPIC_API_KEY=synthetic-anthropic-value",
            "AGENT_EXECUTION_MODE=live",
            "LOCAL_WEB_PORT=60111",
            "NEGOPILOT_DECOMPOSER_BACKEND=anthropic",
            "NEGOPILOT_GENERATOR_BACKEND=anthropic",
        )))
        self.assert_modes(config, "stub")
        self.assert_port(config, 5174)
        self.assertEqual(volume_names(config), volume_names(self.test))
        for service in config["services"].values():
            for key in PROVIDER_KEYS:
                self.assertIn(service.get("environment", {}).get(key), (None, ""))

    def test_test_overlay_discards_provider_credentials_exported_in_the_shell(self):
        config = render(TEST, variables={
            "OPENAI_API_KEY": "synthetic-openai-value",
            "ANTHROPIC_API_KEY": "synthetic-anthropic-value",
            "AGENT_EXECUTION_MODE": "live",
            "TEST_WEB_PORT": "60112",
        })
        self.assert_modes(config, "stub")
        self.assert_port(config, 60112)
        for key in PROVIDER_KEYS:
            self.assertEqual(config["services"]["agent"]["environment"].get(key), "")

    def test_ci_environment_selects_test_overlay_for_every_plain_compose_command(self):
        expected = {
            "COMPOSE_FILE": "compose.yaml:compose.test.yaml",
            "COMPOSE_PROJECT_NAME": "negopilot-ci",
            "TEST_WEB_PORT": "5173",
        }
        for name, value in expected.items():
            self.assertEqual(os.environ.get(name), value, f"Run this suite with the Docker CI job environment: {name}")
        # No -f flags here: config/up/down/logs/cleanup must all inherit the same files.
        config = render((), variables=expected)
        self.assertEqual(config["name"], "negopilot-ci")
        self.assert_modes(config, "stub")
        self.assert_port(config, 5173)
        self.assertEqual(config["services"]["backend"]["environment"]["SERVER_SERVLET_SESSION_COOKIE_NAME"],
                         "NEGOPILOT_TEST_SESSION")
        self.assertTrue(volume_names(config).isdisjoint(volume_names(self.live)))
        self.assertTrue(volume_names(config).isdisjoint(volume_names(self.test)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
