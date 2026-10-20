"""intent 등록표(intents.yaml)를 읽는다. 쿼리 분해와 Routing이 같은 표를 쓴다."""

from functools import cache
from importlib.resources import files
from typing import Literal

import yaml
from pydantic import BaseModel


class IntentSpec(BaseModel):
    intent: str
    server: str | None
    tools: list[str]
    grounding: Literal["required", "none"]
    status: Literal["active", "planned"]
    description: str
    entities: list[str]
    examples: list[str]


@cache
def load_intents() -> dict[str, IntentSpec]:
    raw = yaml.safe_load(files("negopilot_shared").joinpath("intents.yaml").read_text(encoding="utf-8"))
    specs = [IntentSpec.model_validate(item) for item in raw]
    return {s.intent: s for s in specs}
