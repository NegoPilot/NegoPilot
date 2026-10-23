"""Offer proposal: company + conditions → one negotiation offer as JSON or PDF (#231). No model calls."""
import asyncio
import re
from typing import Literal
from uuid import UUID

from fastapi import Depends, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from negopilot_agent.offer_proposal import OfferProposalRequest, OfferProposer
from negopilot_agent.offer_proposal.pdf import render_pdf

# business-mcp input errors that the user can fix; other tool errors stay internal.
INPUT_ERRORS = {"RFQ_NOT_FOUND", "SUPPLIER_NOT_FOUND", "VALUE_OUT_OF_RANGE"}


class OfferProposalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    offer: OfferProposalRequest


def add_offer_routes(app, settings, authenticate, error_response, caller=None):
    @app.post("/internal/offer-proposals", dependencies=[Depends(authenticate)])
    async def propose(body: OfferProposalBody, format: Literal["json", "pdf"] = Query("json")):
        try:
            async with asyncio.timeout(settings.request_timeout_seconds):
                proposal = await OfferProposer(caller).propose(body.offer)
        except TimeoutError:
            return error_response(504, body.request_id, "UPSTREAM_TIMEOUT", "Execution timed out.", True)
        except Exception as exc:
            code = re.match(r"\[([A-Z_]+)\]", str(exc))
            if code and code.group(1) in INPUT_ERRORS:
                return error_response(422, body.request_id, code.group(1), "Offer input was rejected.")
            return error_response(500, body.request_id, "INTERNAL_ERROR", "Unexpected server error.")
        if format == "pdf":
            name = f"offer-{proposal.rfq_id}-{proposal.supplier_id}.pdf"
            return Response(render_pdf(proposal), media_type="application/pdf",
                            headers={"Content-Disposition": f'attachment; filename="{name}"'})
        return JSONResponse({"request_id": str(body.request_id), "proposal": proposal.model_dump(mode="json")})
