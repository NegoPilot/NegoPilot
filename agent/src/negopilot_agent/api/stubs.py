"""Three deterministic decomposition fixtures reused by the Generation stub. No model is created."""

import json
from pathlib import Path

from negopilot_shared import DecompositionResult

FIXTURE_NAMES = ("01_law_article.json", "09_clarify.json", "11_failed.json")
FIXTURE_DIRECTORY = Path(__file__).resolve().parents[4] / "shared" / "fixtures" / "decomposition"


class UnsupportedStubQuery(ValueError):
    pass


class FixtureDecomposer:
    def __init__(self) -> None:
        self.fixtures = {}
        for name in FIXTURE_NAMES:
            fixture = json.loads((FIXTURE_DIRECTORY / name).read_text(encoding="utf-8"))
            self.fixtures[fixture["query"]] = DecompositionResult.model_validate(fixture["expected"])

    async def decompose(self, query: str, history: list[dict] | None = None) -> DecompositionResult:
        if query not in self.fixtures:
            raise UnsupportedStubQuery("Only the three documented fixture queries are supported")
        return self.fixtures[query].model_copy(deep=True)
