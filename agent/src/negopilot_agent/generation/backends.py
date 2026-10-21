"""답변 생성에 쓰는 모델 백엔드.

분해 백엔드(decomposition/backends.py)와 호출 방식이 같고 출력 스키마만 다르다.
그래서 분해 백엔드를 상속해 스키마만 GenerationDraft로 바꾼다.
"""

import anthropic
from openai.lib._pydantic import to_strict_json_schema

from negopilot_agent.decomposition.backends import AnthropicBackend, Backend, OpenAIBackend
from negopilot_agent.generation.answer import GenerationDraft

__all__ = ["Backend", "GenerationAnthropicBackend", "GenerationOpenAIBackend"]


class GenerationAnthropicBackend(AnthropicBackend):
    OUTPUT_FORMAT = {"type": "json_schema",
                     "schema": anthropic.transform_schema(GenerationDraft.model_json_schema())}


class GenerationOpenAIBackend(OpenAIBackend):
    OUTPUT_MODEL = GenerationDraft
    OUTPUT_NAME = GenerationDraft.__name__
    OUTPUT_SCHEMA = to_strict_json_schema(GenerationDraft)
    COMPACT_OUTPUT = False   # 분해용 짧은 출력 형식은 쓰지 않는다

    @staticmethod
    def to_contract(text: str) -> str:
        return text
