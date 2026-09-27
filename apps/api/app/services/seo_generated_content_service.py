"""SEO generated content service — brief-driven draft generation (M9.9)."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.seo_content_generation_agent import SeoContentGenerationAgent, SeoContentGenerationRequest
from app.ai.providers.base import AIGenerationError
from app.ai.providers.factory import AIProviderConfigurationError, get_ai_provider
from app.models.seo_generated_content import SeoGeneratedContent
from app.schemas.client import ClientContext
from app.schemas.seo_generated_content import SeoGeneratedContentSourceOut
from app.security.audit import write_audit
from app.seo.content_generation.context import build_generation_context, count_words, slugify
from app.seo.content_generation.fidelity import FidelityError, validate_generated_content
from app.seo.content_generation.thresholds import (
    ALGORITHM_VERSION,
    DEFAULT_SEO_CONTENT_GENERATION_LIMITS,
    GENERATABLE_BRIEF_STATUSES,
    PROMPT_VERSION,
)
from app.services.seo_content_brief_service import SeoContentBriefService

DISCLAIMER = (
    "Generated SEO content is an AI draft based on a GrowthOS content brief. "
    "It has not been fact-checked externally and must not be published automatically."
)

NO_PUBLISHING_NOTE = "This artifact is a draft only. M9.9 does not publish or modify live websites."


class SeoGeneratedContentService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def generate(
        self,
        *,
        organization_id: UUID,
        content_brief_id: UUID,
        user_id: UUID | None = None,
    ) -> dict:
        brief = await SeoContentBriefService(self.db).get_brief(
            organization_id=organization_id,
            brief_id=content_brief_id,
        )

        if brief.status not in GENERATABLE_BRIEF_STATUSES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="brief_not_eligible")

        if not brief.outline:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="incomplete_brief")

        if not brief.evidence_refs:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="insufficient_evidence")

        ctx = build_generation_context(brief)
        context_json = json.dumps(ctx.as_prompt_dict(), ensure_ascii=False)
        if len(context_json) > DEFAULT_SEO_CONTENT_GENERATION_LIMITS.max_prompt_chars:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="context_too_large")

        generation_key = hashlib.sha256(
            f"{organization_id}|{content_brief_id}|{ctx.context_hash()}|{PROMPT_VERSION}|{ALGORITHM_VERSION}".encode()
        ).hexdigest()[:64]

        await self.db.execute(
            delete(SeoGeneratedContent).where(
                SeoGeneratedContent.organization_id == organization_id,
                SeoGeneratedContent.generation_key == generation_key,
            )
        )

        site_url = brief.target_url or (brief.context_snapshot or {}).get("site_url")

        try:
            provider = get_ai_provider()
            agent = SeoContentGenerationAgent(provider)
            client_ctx = ClientContext(
                client_id=organization_id,
                organization_id=organization_id,
                business_name="SEO",
                industry=None,
                website=site_url,
                description=None,
                location=None,
                target_audience=brief.target_audience if brief.target_audience != "unavailable" else None,
                products_services=None,
                marketing_goals=None,
                monthly_budget=None,
                brand_voice=None,
                competitors=[],
                primary_channels=[],
                kpis=[],
                demo_mode=False,
                available_metrics={},
            )
            output = await agent.run(
                client_ctx,
                SeoContentGenerationRequest(context_json=context_json, site_url=site_url),
            )
            provider_name = getattr(provider, "name", "unknown")
            inner = getattr(provider, "inner", provider)
            model_name = getattr(inner, "model", getattr(inner, "name", "unknown"))
        except AIProviderConfigurationError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="ai_provider_unavailable") from exc
        except AIGenerationError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="ai_generation_failed") from exc

        try:
            generated = validate_generated_content(output, ctx)
        except FidelityError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="brief_fidelity_failed") from exc

        full_text = _render_full_content(generated)
        if len(full_text) > DEFAULT_SEO_CONTENT_GENERATION_LIMITS.max_output_chars:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="output_too_large")

        structured = [s.model_dump() for s in generated.sections]
        row = SeoGeneratedContent(
            organization_id=organization_id,
            content_brief_id=content_brief_id,
            recommendation_id=brief.recommendation_id,
            title=generated.title[:255],
            slug=slugify(generated.title),
            content_type=generated.content_type,
            status="draft",
            content=full_text,
            structured_sections=structured,
            primary_keyword=brief.primary_keyword,
            secondary_keywords=list(brief.secondary_keywords or []),
            target_topic=brief.target_topic,
            target_url=brief.target_url,
            meta_title=generated.meta_title[:70],
            meta_description=generated.meta_description[:160],
            outline_used=list(brief.outline or []),
            internal_link_targets=[l.model_dump() for l in generated.internal_links],
            evidence_refs=list(brief.evidence_refs or []),
            limitations=generated.limitations + output.data_limitations + [NO_PUBLISHING_NOTE],
            brief_snapshot=ctx.as_prompt_dict(),
            provider=provider_name,
            model=model_name,
            prompt_version=PROMPT_VERSION,
            algorithm_version=ALGORITHM_VERSION,
            generation_key=generation_key,
            word_count=count_words(full_text),
        )
        self.db.add(row)
        await self.db.flush()

        await write_audit(
            self.db,
            action="seo_content.generate",
            organization_id=organization_id,
            user_id=user_id,
            resource_type="seo_generated_content",
            resource_id=str(row.id),
            details={"content_brief_id": str(content_brief_id), "generation_key": generation_key},
        )
        await self.db.flush()

        return {
            "content_id": row.id,
            "content_brief_id": content_brief_id,
            "recommendation_id": brief.recommendation_id,
            "algorithm_version": ALGORITHM_VERSION,
            "prompt_version": PROMPT_VERSION,
            "provider": provider_name,
            "status": "draft",
        }

    async def list_content(
        self,
        *,
        organization_id: UUID,
        content_brief_id: UUID | None = None,
        content_type: str | None = None,
        status_filter: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SeoGeneratedContent]:
        q = select(SeoGeneratedContent).where(
            SeoGeneratedContent.organization_id == organization_id,
            SeoGeneratedContent.algorithm_version == ALGORITHM_VERSION,
        )
        if content_brief_id:
            q = q.where(SeoGeneratedContent.content_brief_id == content_brief_id)
        if content_type:
            q = q.where(SeoGeneratedContent.content_type == content_type)
        if status_filter:
            q = q.where(SeoGeneratedContent.status == status_filter)
        q = q.order_by(SeoGeneratedContent.created_at.desc()).offset(offset).limit(min(limit, 500))
        return list((await self.db.execute(q)).scalars().all())

    async def get_content(self, *, organization_id: UUID, content_id: UUID) -> SeoGeneratedContent:
        row = await self.db.scalar(
            select(SeoGeneratedContent).where(
                SeoGeneratedContent.id == content_id,
                SeoGeneratedContent.organization_id == organization_id,
            ).limit(1)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SEO generated content not found")
        return row

    async def get_source(self, *, organization_id: UUID, content_id: UUID) -> SeoGeneratedContentSourceOut:
        row = await self.get_content(organization_id=organization_id, content_id=content_id)
        return SeoGeneratedContentSourceOut(
            content_id=row.id,
            content_brief_id=row.content_brief_id,
            recommendation_id=row.recommendation_id,
            brief_snapshot=row.brief_snapshot,
            evidence_refs=row.evidence_refs,
            limitations=row.limitations,
            disclaimer=DISCLAIMER,
        )

    async def archive_content(self, *, organization_id: UUID, content_id: UUID) -> SeoGeneratedContent:
        row = await self.get_content(organization_id=organization_id, content_id=content_id)
        row.status = "archived"
        await self.db.flush()
        return row


def _render_full_content(generated) -> str:
    parts = [f"# {generated.title}", "", generated.introduction, ""]
    for section in generated.sections:
        parts.append(f"## {section.heading}")
        parts.append(section.content)
        for sub in section.subsections:
            parts.append(f"### {sub.heading}")
            parts.append(sub.content)
        parts.append("")
    parts.append("## Conclusion")
    parts.append(generated.conclusion)
    return "\n".join(parts).strip()
