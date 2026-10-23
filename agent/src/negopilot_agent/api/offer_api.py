"""Offer proposals (#231, #236): company + conditions → supplier proposal (PDF/DOCX) and internal briefing.

Generate, re-render an edited OfferDocument (re-checked with check_offer), or run an Excel/CSV batch.
Contracts: docs/contracts/offer-proposal.md, docs/contracts/offer-document.md
"""
import asyncio
import base64
import hashlib
import json
import re
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from negopilot_agent.offer_proposal import excel, supplier
from negopilot_agent.offer_proposal.document import OfferDocument
from negopilot_agent.offer_proposal.pdf import briefing_filename, render_briefing_pdf
from negopilot_agent.offer_proposal.request import OfferProposalRequest
from negopilot_agent.offer_proposal.service import OfferViolation, build_package, package_files, recheck
from negopilot_agent.offer_proposal.writer import NegotiationWriter, writing_enabled

# business-mcp input errors that the user can fix; other tool errors stay internal.
INPUT_ERRORS = {"RFQ_NOT_FOUND", "SUPPLIER_NOT_FOUND", "VALUE_OUT_OF_RANGE"}
MAX_UPLOAD_BYTES = 2_000_000
MAX_BATCH_ROWS = 50
MAX_ATTACHMENT_BYTES = 10_000_000   # mail.md 2절: 첨부 하나 10MB 이하
FILE_SETS = {"pdf": ("pdf",), "docx": ("docx",), "both": ("pdf", "docx")}


class OfferProposalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    offer: OfferProposalRequest


class OfferRenderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    document: OfferDocument
    files: Literal["pdf", "docx", "both"] = "both"     # 메일에 붙일 첨부. content_hash도 이 파일들로 계산한다


def file_response(data: bytes, filename: str, media_type: str, headers: dict | None = None) -> Response:
    return Response(data, media_type=media_type, headers={
        "Content-Disposition": supplier.content_disposition(filename),
        "X-File-Sha256": hashlib.sha256(data).hexdigest(), **(headers or {})})


def checked_attachments(document: OfferDocument, kinds) -> list[tuple[str, str, bytes]]:
    files = supplier.attachments(document, kinds)
    for name, _, data in files:
        if "/" in name or "\\" in name or len(data) > MAX_ATTACHMENT_BYTES:
            raise ValueError(f"attachment {name!r} breaks the mail contract")
    return files


def document_file(document: OfferDocument, fmt: str) -> Response:
    """제안서 파일 하나. X-Content-Hash는 이 파일 하나를 첨부로 본 content_hash다 (mail.md 3절)."""
    [(name, media, data)] = checked_attachments(document, (fmt,))
    hashed = document.hashed([(name, media, data)])
    return file_response(data, name, media,
                         {"X-Content-Hash": hashed.content_hash, "X-Document-Version": str(document.version)})


def summary_line(pkg) -> dict:
    o = pkg.proposal.offer
    if o is None:
        return {"status": "no_offer", "error": "; ".join(pkg.proposal.no_offer_reasons)[:300]}
    return {"status": "offer", "unit_price": o.unit_price, "payment_days": o.payment_days,
            "linkage": o.linkage_status, "warnings": len(o.warnings),
            "input_needed": "; ".join(x.message for x in pkg.proposal.input_needed),
            "content_hash": pkg.document.content_hash}


def add_offer_routes(app, settings, authenticate, error_response, caller=None, writer_factory=None):
    def make_writer(enabled: bool) -> NegotiationWriter | None:
        if not enabled or not writing_enabled():
            return None
        return writer_factory() if writer_factory else NegotiationWriter()

    def tool_error(exc: Exception, request_id) -> JSONResponse:
        code = re.match(r"\[([A-Z_]+)\]", str(exc))
        if code and code.group(1) in INPUT_ERRORS:
            return error_response(422, request_id, code.group(1), "Offer input was rejected.")
        return error_response(500, request_id, "INTERNAL_ERROR", "Unexpected server error.")

    @app.post("/internal/offer-proposals", dependencies=[Depends(authenticate)])
    async def propose(body: OfferProposalBody,
                      format: Literal["json", "pdf", "docx", "briefing_pdf"] = Query("json"),
                      writing: bool = Query(True)):
        try:
            async with asyncio.timeout(settings.request_timeout_seconds):
                pkg = await build_package(body.offer, caller, make_writer(writing))
        except TimeoutError:
            return error_response(504, body.request_id, "UPSTREAM_TIMEOUT", "Execution timed out.", True)
        except Exception as exc:
            return tool_error(exc, body.request_id)
        if format == "briefing_pdf":
            return file_response(render_briefing_pdf(pkg.proposal, pkg.writing), briefing_filename(pkg.proposal),
                                 "application/pdf")
        if format in ("pdf", "docx"):
            if pkg.document is None:
                return error_response(422, body.request_id, "NO_OFFER", "No offer passed the legal check.")
            return document_file(pkg.document, format)
        return JSONResponse({"request_id": str(body.request_id),
                             "offer_document": pkg.document.model_dump(mode="json") if pkg.document else None,
                             "check": pkg.check(), "internal_briefing": pkg.briefing()})

    @app.post("/internal/offer-proposals/render", dependencies=[Depends(authenticate)])
    async def render(body: OfferRenderBody, format: Literal["json", "pdf", "docx"] = Query("json")):
        try:
            async with asyncio.timeout(settings.request_timeout_seconds):
                document, check = await recheck(body.document, caller)
        except OfferViolation as exc:
            rejected = error_response(422, body.request_id, "OFFER_VIOLATION", "The edited terms violate a rule.")
            return JSONResponse(status_code=422, content=json.loads(rejected.body) | {"findings": exc.findings})
        except TimeoutError:
            return error_response(504, body.request_id, "UPSTREAM_TIMEOUT", "Execution timed out.", True)
        except Exception as exc:
            return tool_error(exc, body.request_id)
        if format in ("pdf", "docx"):
            return document_file(document, format)
        files = checked_attachments(document, FILE_SETS[body.files])
        hashed = document.hashed(files)
        return JSONResponse({"request_id": str(body.request_id), "document": hashed.model_dump(mode="json"),
                             "attachments": [{"filename": name, "content_type": media,
                                              "data_base64": base64.b64encode(data).decode("ascii")}
                                             for name, media, data in files],
                             "check": check})

    @app.post("/internal/offer-proposals/batch", dependencies=[Depends(authenticate)])
    async def batch(request: Request, filename: str = Query(pattern=r"(?i)^[^/\\]{1,120}\.(xlsx|csv)$"),
                    writing: bool = Query(True)):
        """요청 본문은 업로드한 파일 바이트 그대로다 (multipart가 아님). 응답은 PDF·DOCX와 summary.csv를 담은 zip."""
        request_id = uuid4()
        data = await request.body()
        if not data or len(data) > MAX_UPLOAD_BYTES:
            return error_response(400, request_id, "VALIDATION_ERROR", "Upload is empty or too large.")
        today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
        try:
            rows = excel.parse_rows(data, filename, today)
        except Exception:
            return error_response(400, request_id, "VALIDATION_ERROR", "The file could not be read.")
        if not rows or len(rows) > MAX_BATCH_ROWS:
            return error_response(400, request_id, "VALIDATION_ERROR", f"1 to {MAX_BATCH_ROWS} rows are required.")
        writer = make_writer(writing)

        async def produce(req: OfferProposalRequest):
            pkg = await build_package(req, caller, writer)
            return summary_line(pkg), package_files(pkg)

        try:
            async with asyncio.timeout(settings.request_timeout_seconds * len(rows)):
                summary, files = await excel.run_batch(rows, produce)
        except TimeoutError:
            return error_response(504, request_id, "UPSTREAM_TIMEOUT", "Execution timed out.", True)
        return file_response(excel.zip_bytes(files, summary), "협상안_일괄.zip", "application/zip")
