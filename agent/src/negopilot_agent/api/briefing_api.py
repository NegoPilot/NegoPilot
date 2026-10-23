"""저장된 내부 브리핑을 그대로 PDF로 렌더한다. 모델과 MCP는 호출하지 않는다."""

from datetime import date
from uuid import UUID

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, field_validator

from negopilot_agent.offer_proposal.generator import Offer, Proposal
from negopilot_agent.offer_proposal.pdf import BriefingState, briefing_filename, render_briefing_pdf
from negopilot_agent.offer_proposal.writer import Writing

from .offer_api import file_response


class BriefingOffer(Offer):
    model_config = ConfigDict(extra='forbid')
    violations: list[dict] | None = None


class BriefingWriting(Writing):
    model_config = ConfigDict(extra='forbid')


class StoredBriefing(Proposal):
    model_config = ConfigDict(extra='forbid')
    offer: BriefingOffer | None
    writing: BriefingWriting | None = None

    @field_validator('as_of')
    @classmethod
    def valid_date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value


class BriefingRenderBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    briefing: StoredBriefing
    state: BriefingState | None = None
    issued_on: date | None = None


def add_briefing_routes(app, authenticate):
    @app.post('/internal/offer-proposals/briefing/render', dependencies=[Depends(authenticate)])
    async def render_briefing(body: BriefingRenderBody):
        day = body.issued_on or date.fromisoformat(body.briefing.as_of)
        data = render_briefing_pdf(body.briefing, body.briefing.writing, state=body.state, fixed_date=day)
        revision = body.state.current_revision if body.state is not None else None
        return file_response(data, briefing_filename(body.briefing, revision), 'application/pdf')
