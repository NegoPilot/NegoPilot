"""Validate Docker AI configuration offline before replacing this process with Uvicorn.

An empty MODEL means the selected provider's existing core default in Docker.
This wrapper does not change the behavior of direct Python/uvicorn execution.
No clients are created and no provider credentials or configuration values are logged.
"""

import math
import os
import sys
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    pass


def _required_token(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise ConfigurationError(f"{name} must be set without whitespace or control characters.")
    return value


def _positive_number(env: Mapping[str, str], name: str, default: str, maximum: float | None = None) -> None:
    try:
        value = float(env.get(name, default))
        valid = math.isfinite(value) and value > 0 and (maximum is None or value <= maximum)
    except (ValueError, OverflowError):
        valid = False
    if not valid:
        raise ConfigurationError(f"{name} must be a finite positive number within the supported range.")


def _validate_ollama(env: Mapping[str, str]) -> None:
    try:
        origin = env.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434").strip()
        parsed = urlsplit(origin)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                 and parsed.username is None and parsed.password is None
                 and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
                 and not any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in origin))
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ConfigurationError("OLLAMA_BASE_URL must be an HTTP origin without credentials, query or fragment.")
    _positive_number(env, "OLLAMA_TIMEOUT_SECONDS", "300", 3600)
    try:
        num_ctx = int(env.get("OLLAMA_NUM_CTX", "32768"))
        num_predict = int(env.get("OLLAMA_NUM_PREDICT", "4096"))
        valid = 1024 <= num_ctx <= 131072 and 1 <= num_predict < num_ctx
    except ValueError:
        valid = False
    if not valid:
        raise ConfigurationError("OLLAMA_NUM_CTX must be 1024..131072 and OLLAMA_NUM_PREDICT must be positive and smaller.")


def prepare_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Return validated settings. Missing/invalid live settings never select stub mode."""
    env = dict(environment)
    mode = env.setdefault("AGENT_EXECUTION_MODE", "live")
    if mode not in {"live", "stub"}:
        raise ConfigurationError("AGENT_EXECUTION_MODE must be live or stub.")
    _required_token(env, "AGENT_INTERNAL_API_KEY")
    _positive_number(env, "AGENT_REQUEST_TIMEOUT_SECONDS", "300")
    if mode == "stub":
        return env  # Fixture models do not require or inspect provider configuration.

    # Import constants only. Settings and clients are not instantiated here.
    from negopilot_agent.decomposition.decomposer import DEFAULT_MODELS as decomposition_models
    from negopilot_agent.generation.generator import DEFAULT_MODELS as generation_models

    providers = set()
    for phase, defaults, effort in (("DECOMPOSER", decomposition_models, "low"),
                                    ("GENERATOR", generation_models, "medium")):
        prefix = f"NEGOPILOT_{phase}"
        provider = env.setdefault(f"{prefix}_BACKEND", "openai")
        if provider not in {"openai", "anthropic", "ollama"}:
            raise ConfigurationError(f"{prefix}_BACKEND must be openai, anthropic or ollama.")
        providers.add(provider)
        model_name = f"{prefix}_MODEL"
        if env.get(model_name, "") == "":
            env[model_name] = defaults[provider]
        _required_token(env, model_name)
        env.setdefault(f"{prefix}_EFFORT", effort)
        env.setdefault(f"{prefix}_FALLBACKS", "1")
        if provider == "anthropic":
            if env[f"{prefix}_EFFORT"] not in {"low", "medium", "high", "xhigh", "max"}:
                raise ConfigurationError(f"{prefix}_EFFORT is not a supported Anthropic effort.")
            if env[f"{prefix}_FALLBACKS"] not in {"0", "1"}:
                raise ConfigurationError(f"{prefix}_FALLBACKS must be 0 or 1.")
    for provider, key in (("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")):
        if provider in providers:
            _required_token(env, key)
    if "ollama" in providers:
        env.setdefault("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
        _validate_ollama(env)
    return env


def main(command: Sequence[str] | None = None) -> int:
    try:
        prepared = prepare_environment(os.environ)
    except ConfigurationError as error:
        print(f"Agent configuration error: {error}", file=sys.stderr)
        return 2
    args = list(sys.argv[1:] if command is None else command)
    if not args:
        print("Agent startup command is required.", file=sys.stderr)
        return 2
    try:
        os.execvpe(args[0], args, prepared)
    except OSError:
        print("Unable to start the Agent process.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
