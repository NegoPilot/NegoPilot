"""Ollama native structured output transport shared by both model phases."""

import math
import os
from urllib.parse import urlsplit

import httpx2

from negopilot_shared import DecompositionResult


def ollama_json_schema(value):
    """Copy the schema and expand digit shorthand unsupported by Ollama's GBNF.

    Keep types, unions, references and constraints. The original Pydantic model
    remains the final validator. See llama.cpp issue #27580.
    """
    if isinstance(value, list):
        return [ollama_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, child in value.items():
        if key == "pattern" and isinstance(child, str):
            pattern, in_class, index = [], False, 0
            while index < len(child):
                character = child[index]
                if character == "\\" and index + 1 < len(child):
                    escaped = child[index + 1]
                    pattern.append(("0-9" if in_class else "[0-9]") if escaped == "d" else child[index:index + 2])
                    index += 2
                    continue
                if character == "[":
                    in_class = True
                elif character == "]":
                    in_class = False
                pattern.append(character)
                index += 1
            result[key] = "".join(pattern)
        else:
            result[key] = ollama_json_schema(child)
    return result


class OllamaBackend:
    OUTPUT_SCHEMA = DecompositionResult.model_json_schema()

    def __init__(self, http: httpx2.AsyncClient, model: str, max_tokens: int, *,
                 num_ctx: int = 32768, num_predict: int = 4096):
        self.http = http
        self.model = model
        self.num_ctx = num_ctx
        self.num_predict = min(max_tokens, num_predict)

    @classmethod
    def from_env(cls, model: str, max_tokens: int):
        if not model.strip() or type(max_tokens) is not int or max_tokens < 1:
            raise ValueError("Invalid Ollama model configuration")
        base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip()
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("OLLAMA_BASE_URL must be an HTTP origin without credentials")
        parsed.port  # Validate the port before constructing a client.
        timeout = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "300"))
        num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "32768"))
        num_predict = int(os.getenv("OLLAMA_NUM_PREDICT", "4096"))
        if not math.isfinite(timeout) or not 0 < timeout <= 3600:
            raise ValueError("Invalid OLLAMA_TIMEOUT_SECONDS")
        if not 1024 <= num_ctx <= 131072 or not 1 <= num_predict < num_ctx:
            raise ValueError("Invalid Ollama token limits")
        http = httpx2.AsyncClient(base_url=base_url.rstrip("/") + "/",
                                 timeout=httpx2.Timeout(timeout, connect=min(5.0, timeout)),
                                 follow_redirects=False, trust_env=False)
        return cls(http, model, max_tokens, num_ctx=num_ctx, num_predict=num_predict)

    async def generate(self, system: str, user: str) -> str | None:
        # The native endpoint has no transport retry and receives no cloud API key.
        response = await self.http.post("api/chat", json={
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": ollama_json_schema(self.OUTPUT_SCHEMA),
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "num_ctx": self.num_ctx, "num_predict": self.num_predict},
        })
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError:
            raise httpx2.RemoteProtocolError("Invalid Ollama response", request=response.request) from None
        if not isinstance(body, dict) or body.get("error") is not None or body.get("done") is not True:
            raise httpx2.RemoteProtocolError("Invalid Ollama response", request=response.request)
        if body.get("done_reason") == "length":
            return None
        message = body.get("message")
        if (not isinstance(message, dict) or message.get("role") != "assistant"
                or not isinstance(message.get("content"), str)):
            raise httpx2.RemoteProtocolError("Invalid Ollama response", request=response.request)
        return message["content"]
