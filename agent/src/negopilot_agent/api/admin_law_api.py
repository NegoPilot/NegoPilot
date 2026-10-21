"""Authenticated read-only metadata adapter for the administrator law screen."""

import asyncio
import re
from typing import Any

from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from negopilot_agent.routing.mcp_client import McpToolCaller


class LawReadModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class LawSummary(LawReadModel):
    law_key: str
    law_name: str
    version_count: int = Field(ge=0)
    article_count: int = Field(ge=0)


class LawCatalog(LawReadModel):
    dataset_id: str
    loaded_at: str
    collected_at: str | None
    law_count: int = Field(ge=0)
    version_count: int = Field(ge=0)
    article_count: int = Field(ge=0)
    items: list[LawSummary]


class VersionSummary(LawReadModel):
    version_id: str
    effective_date: str
    promulgation_date: str
    promulgation_no: str
    source_url: str
    source_commit: str
    article_count: int = Field(ge=0)


class LawVersions(LawReadModel):
    law_key: str
    law_name: str
    items: list[VersionSummary]


class ArticleSummary(LawReadModel):
    no: str
    title: str
    deleted: bool


class ArticlePage(VersionSummary):
    law_key: str
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    items: list[ArticleSummary]


class Paragraph(LawReadModel):
    no: str | None
    text: str


class Article(ArticleSummary):
    chapter: str | None
    section: str | None
    paragraphs: list[Paragraph]
    text: str


class ArticleDetail(VersionSummary):
    law_key: str
    law_name: str
    article: Article
    citation: str


def add_admin_law_routes(app: FastAPI, settings, authenticate, error_response, caller=None):
    caller = caller or McpToolCaller()

    def validate(request: Request, args: dict[str, Any], paged=False):
        pairs = list(request.query_params.multi_items())
        if len(dict(pairs)) != len(pairs) or set(dict(pairs)) - ({"offset", "limit"} if paged else set()):
            raise ValueError("Invalid query")
        if "law_key" in args and not re.fullmatch(r"[a-z_]{1,80}", args["law_key"]):
            raise ValueError("Invalid law key")
        if "version_id" in args and not re.fullmatch(r"\d{4}-\d{2}-\d{2}_[a-f0-9]{40}", args["version_id"]):
            raise ValueError("Invalid version")
        if "article_no" in args and not re.fullmatch(r"제?\d+(?:조)?(?:의\d+)?", args["article_no"]):
            raise ValueError("Invalid article")
        if paged:
            for key, default, upper in (("offset", 0, 100000), ("limit", 50, 100)):
                raw = request.query_params.get(key, str(default))
                if not re.fullmatch(r"[0-9]{1,6}", raw) or not (0 if key == "offset" else 1) <= int(raw) <= upper:
                    raise ValueError("Invalid page")
                args[key] = int(raw)

    async def run(request, tool, args, model, paged=False):
        try:
            validate(request, args, paged)
        except ValueError:
            return error_response(400, None, "VALIDATION_ERROR", "Invalid law request.")
        try:
            async with asyncio.timeout(settings.request_timeout_seconds):
                raw = await caller("law-mcp", tool, args)
            if raw is None or raw == {"result": None}:
                return error_response(404, None, "LAW_NOT_FOUND", "Law data not found.")
            parsed = model.model_validate(raw)
            if "law_key" in args and parsed.law_key != args["law_key"]:
                raise ValueError("Wrong law")
            if "version_id" in args and parsed.version_id != args["version_id"]:
                raise ValueError("Wrong version")
            if isinstance(parsed, LawCatalog) and parsed.law_count != len(parsed.items):
                raise ValueError("Wrong count")
            if isinstance(parsed, ArticlePage) and (parsed.offset != args["offset"] or parsed.limit != args["limit"]):
                raise ValueError("Wrong page")
            if isinstance(parsed, ArticleDetail):
                digits = re.fullmatch(r"제?(\d+)(?:조)?(?:의(\d+))?", args["article_no"])
                expected = f"제{digits[1]}조" + (f"의{digits[2]}" if digits[2] else "")
                if parsed.article.no != expected:
                    raise ValueError("Wrong article")
            return {"schema_version": "1.0", "data": parsed.model_dump()}
        except TimeoutError:
            return error_response(504, None, "UPSTREAM_TIMEOUT", "Law service timed out.", True)
        except (ValidationError, ValueError, TypeError):
            return error_response(502, None, "UPSTREAM_INVALID_RESPONSE", "Invalid law service response.")
        except Exception:
            return error_response(503, None, "UPSTREAM_UNAVAILABLE", "Law service unavailable.", True)

    deps = [Depends(authenticate)]

    @app.get("/internal/v1/admin/laws", dependencies=deps)
    async def catalog(request: Request):
        return await run(request, "admin_law_catalog", {}, LawCatalog)

    @app.get("/internal/v1/admin/laws/{law_key}/versions", dependencies=deps)
    async def versions(law_key: str, request: Request):
        return await run(request, "admin_law_versions", {"law_key": law_key}, LawVersions)

    @app.get("/internal/v1/admin/laws/{law_key}/versions/{version_id}/articles", dependencies=deps)
    async def articles(law_key: str, version_id: str, request: Request):
        return await run(request, "admin_law_articles", {"law_key": law_key, "version_id": version_id}, ArticlePage, True)

    @app.get("/internal/v1/admin/laws/{law_key}/versions/{version_id}/articles/{article_no}", dependencies=deps)
    async def article(law_key: str, version_id: str, article_no: str, request: Request):
        return await run(request, "admin_law_article", {"law_key": law_key, "version_id": version_id,
                                                       "article_no": article_no}, ArticleDetail)
