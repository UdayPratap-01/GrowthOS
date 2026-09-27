"""SEO content brief service — recommendation-driven brief generation (M9.8)."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.seo_content_brief_agent import SeoContentBriefAgent, SeoContentBriefRequest
from app.ai.providers.base import AIGenerationError
from app.ai.providers.factory import AIProviderConfigurationError, get_ai_provider
from app.models.seo_content_brief import SeoContentBrief
from app.schemas.client import ClientContext
from app.security.audit import write_audit
from app.seo.content_briefs.context import build_brief_context
from app.seo.content_briefs.grounding import BriefGroundingError, validate_brief_output
from app.seo.content_briefs.thresholds import (
    ALGORITHM_VERSION,
    BRIEF_ELIGIBLE_RECOMMENDATION_TYPES,
    DEFAULT_SEO_CONTENT_BRIEF_LIMITS,
    PROMPT_VERSION,
)
from app.services.seo_recommendation_service import SeoRecommendationService

DISCLAIMER = (
    "SEO content briefs are AI-generated planning artifacts based on grounded GrowthOS evidence. "
    "They are not final articles and do not guarantee rankings or traffic."
)


class SeoContentBriefService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def generate(
        self,
        *,
        organization_id: UUID,
        recommendation_id: UUID,
        user_id: UUID | None = None,
    ) -> dict:
        recommendation = await SeoRecommendationService(self.db).get_recommendation(
            organization_id=organization_id,
            recommendation_id=recommendation_id,
        )

        if recommendation.recommendation_type not in BRIEF_ELIGIBLE_RECOMMENDATION_TYPES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported_recommendation_type")

        if not recommendation.evidence_refs:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="insufficient_evidence")

        ctx = await build_brief_context(self.db, organization_id=organization_id, recommendation=recommendation)
        if not ctx.resolved_evidence:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="insufficient_evidence")

        context_json = json.dumps(ctx.as_prompt_dict(), ensure_ascii=False)
        if len(context_json) > DEFAULT_SEO_CONTENT_BRIEF_LIMITS.max_prompt_chars:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="context_too_large")

        generation_key = hashlib.sha256(
            f"{organization_id}|{recommendation_id}|{ctx.context_hash()}|{PROMPT_VERSION}|{ALGORITHM_VERSION}".encode()
        ).hexdigest()[:64]

        await self.db.execute(
            delete(SeoContentBrief).where(
                SeoContentBrief.organization_id == organization_id,
                SeoContentBrief.generation_key == generation_key,
            )
        )

        try:
            provider = get_ai_provider()
            agent = SeoContentBriefAgent(provider)
            client_ctx = ClientContext(
                client_id=organization_id,
                organization_id=organization_id,
                business_name="SEO",
                industry=None,
                website=ctx.site_url,
                description=None,
                location=None,
                target_audience=None,
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
                SeoContentBriefRequest(context_json=context_json, site_url=ctx.site_url),
            )
            provider_name = getattr(provider, "name", "unknown")
            inner = getattr(provider, "inner", provider)
            model_name = getattr(inner, "model", getattr(inner, "name", "unknown"))
        except AIProviderConfigurationError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="ai_provider_unavailable") from exc
        except AIGenerationError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="ai_generation_failed") from exc

        try:
            brief = validate_brief_output(output, ctx)
        except BriefGroundingError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="grounding_validation_failed") from exc

        row = SeoContentBrief(
            organization_id=organization_id,
            recommendation_id=recommendation_id,
            title=brief.title[:255],
            brief_type=brief.brief_type,
            primary_keyword=brief.primary_keyword[:255],
            secondary_keywords=brief.secondary_keywords,
            target_topic=brief.target_topic,
            search_intent=brief.search_intent.model_dump(),
            target_url=brief.target_url,
            content_goal=brief.content_goal,
            target_audience=brief.target_audience,
            suggested_content_type=brief.suggested_content_type,
            suggested_angle=brief.suggested_angle,
            outline=[s.model_dump() for s in brief.outline],
            questions_to_answer=brief.questions_to_answer,
            entities_to_cover=brief.entities_to_cover,
            internal_link_targets=brief.internal_link_targets,
            competitor_context=ctx.competitor_context,
            evidence_refs=[r.model_dump() for r in brief.evidence_refs],
            source_recommendation_ids=[str(recommendation_id)],
            content_requirements=brief.content_requirements,
            seo_requirements=brief.seo_requirements,
            limitations=brief.limitations + output.data_limitations,
            context_snapshot=ctx.as_prompt_dict(),
            status="ready",
            provider=provider_name,
            model=model_name,
            prompt_version=PROMPT_VERSION,
            algorithm_version=ALGORITHM_VERSION,
            generation_key=generation_key,
        )
        self.db.add(row)
        await self.db.flush()

        await write_audit(
            self.db,
            action="seo_content_brief.generate",
            organization_id=organization_id,
            user_id=user_id,
            resource_type="seo_content_brief",
            resource_id=str(row.id),
            details={"recommendation_id": str(recommendation_id), "generation_key": generation_key},
        )
        await self.db.flush()

        return {
            "brief_id": row.id,
            "recommendation_id": recommendation_id,
            "algorithm_version": ALGORITHM_VERSION,
            "prompt_version": PROMPT_VERSION,
            "provider": provider_name,
        }

    async def list_briefs(
        self,
        *,
        organization_id: UUID,
        recommendation_id: UUID | None = None,
        brief_type: str | None = None,
        status_filter: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SeoContentBrief]:
        q = select(SeoContentBrief).where(
            SeoContentBrief.organization_id == organization_id,
            SeoContentBrief.algorithm_version == ALGORITHM_VERSION,
        )
        if recommendation_id:
            q = q.where(SeoContentBrief.recommendation_id == recommendation_id)
        if brief_type:
            q = q.where(SeoContentBrief.brief_type == brief_type)
        if status_filter:
            q = q.where(SeoContentBrief.status == status_filter)
        q = q.order_by(SeoContentBrief.created_at.desc()).offset(offset).limit(min(limit, 500))
        return list((await self.db.execute(q)).scalars().all())

    async def get_brief(self, *, organization_id: UUID, brief_id: UUID) -> SeoContentBrief:
        row = await self.db.scalar(
            select(SeoContentBrief).where(
                SeoContentBrief.id == brief_id,
                SeoContentBrief.organization_id == organization_id,
            ).limit(1)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SEO content brief not found")
        return row

    async def archive_brief(self, *, organization_id: UUID, brief_id: UUID) -> SeoContentBrief:
        row = await self.get_brief(organization_id=organization_id, brief_id=brief_id)
        row.status = "archived"
        await self.db.flush()
        return row
