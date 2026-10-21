"""답변 생성에 쓰는 모델 백엔드.

분해 백엔드(decomposition/backends.py)와 호출 방식이 같고 출력 스키마만 다르다.
그래서 분해 백엔드를 상속해 스키마만 GenerationDraft로 바꾼다.
"""

import anthropic

from negopilot_agent.decomposition.backends import AnthropicBackend, Backend, OllamaBackend, inline_refs
from negopilot_agent.generation.answer import GenerationDraft

__all__ = ["Backend", "GenerationAnthropicBackend", "GenerationOllamaBackend"]


class GenerationAnthropicBackend(AnthropicBackend):
    OUTPUT_FORMAT = {"type": "json_schema",
                     "schema": anthropic.transform_schema(GenerationDraft.model_json_schema())}


class GenerationOllamaBackend(OllamaBackend):
    OUTPUT_SCHEMA = inline_refs(GenerationDraft.model_json_schema())
