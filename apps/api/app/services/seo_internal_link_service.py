"""SEO internal-link engine service (M9.12)."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.seo_internal_link_agent import SeoInternalLinkAgent, SeoInternalLinkRequest
from app.ai.providers.base import AIGenerationError
from app.ai.providers.factory import AIProviderConfigurationError, get_ai_provider
from app.models.seo_content_brief import SeoContentBrief
from app.models.seo_generated_content import SeoGeneratedContent
from app.models.seo_internal_link import SeoInternalLinkOpportunity, SeoInternalLinkRun
from app.schemas.client import ClientContext
from app.schemas.seo_internal_link import (
    SeoInternalLinkOpportunityOut,
    SeoInternalLinkReportOut,
    SeoInternalLinkRunOut,
    SeoInternalLinkSummaryOut,
)
from app.security.audit import write_audit
from app.seo.internal_links.context import build_internal_link_context
from app.seo.internal_links.engine import NO_PUBLISHING, discover_internal_links
from app.seo.internal_links.grounding import InternalLinkGroundingError, apply_ai_enrichment
from app.seo.internal_links.thresholds import ALGORITHM_VERSION, DEFAULT_INTERNAL_LINK_LIMITS, PROMPT_VERSION
from app.seo.internal_links.types import InternalLinkOpportunityDraft
from app.services.seo_content_brief_service import SeoContentBriefService
from app.services.seo_generated_content_service import SeoGeneratedContentService

DISCLAIMER = (
    "Internal-link recommendations are planning artifacts for GrowthOS draft content. "
    "They do not modify live websites and must not be auto-published."
)


class SeoInternalLinkService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def generate(
        self,
        *,
        organization_id: UUID,
        content_id: UUID,
        user_id: UUID | None = None,
        use_ai: bool = True,
    ) -> dict:
        content = await SeoGeneratedContentService(self.db).get_content(
            organization_id=organization_id, content_id=content_id
        )
        if content.status == "archived":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="content_archived")

        brief = await SeoContentBriefService(self.db).get_brief(
            organization_id=organization_id, brief_id=content.content_brief_id
        )

        analysis_key = _analysis_key(organization_id=organization_id, content=content, brief=brief)

        await self.db.execute(
            delete(SeoInternalLinkRun).where(
                SeoInternalLinkRun.organization_id == organization_id,
                SeoInternalLinkRun.analysis_key == analysis_key,
            )
        )

        ctx = await build_internal_link_context(
            self.db,
            organization_id=organization_id,
            content=content,
            brief=brief,
            limits=DEFAULT_INTERNAL_LINK_LIMITS,
        )
        drafts = discover_internal_links(ctx)

        ai_enriched = False
        provider_name = "none"
        model_name = "none"
        ai_limitations: list[str] = []

        if use_ai and drafts:
            try:
                provider = get_ai_provider()
                provider_name = getattr(provider, "name", "unknown")
                inner = getattr(provider, "inner", provider)
                model_name = getattr(inner, "model", getattr(inner, "name", "unknown"))
                agent = SeoInternalLinkAgent(provider)
                payload = [
                    {
                        "source_url": d.source_url,
                        "target_url": d.target_url,
                        "anchor_text": d.anchor_text,
                        "opportunity_type": d.opportunity_type,
                        "relevance_score": d.relevance_score,
                    }
                    for d in drafts[: DEFAULT_INTERNAL_LINK_LIMITS.max_ai_suggestions]
                ]
                ctx_json = json.dumps(payload, ensure_ascii=False)
                if len(ctx_json) <= DEFAULT_INTERNAL_LINK_LIMITS.max_prompt_chars:
                    client_ctx = ClientContext(
                        client_id=organization_id,
                        organization_id=organization_id,
                        business_name="SEO",
                        industry=None,
                        website=content.target_url,
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
                        SeoInternalLinkRequest(
                            opportunities_json=ctx_json,
                            content_summary=_content_summary(content),
                            brief_summary=_brief_summary(brief),
                        ),
                    )
                    apply_ai_enrichment(drafts, output, site_root=ctx.site_root)
                    ai_limitations = list(output.limitations)
                    ai_enriched = bool(output.suggestions)
            except (AIProviderConfigurationError, AIGenerationError, InternalLinkGroundingError):
                ai_limitations.append("AI enrichment unavailable; deterministic recommendations only.")

        stats = _stats_from_drafts(drafts)
        limitations = [NO_PUBLISHING, "M9.12 does not implement approval or live-site link injection (M9.13)."]
        limitations.extend(ctx.limitations)
        limitations.extend(ai_limitations)

        run = SeoInternalLinkRun(
            organization_id=organization_id,
            generated_content_id=content.id,
            content_brief_id=brief.id,
            crawl_id=ctx.crawl_id,
            status="completed",
            analysis_key=analysis_key,
            stats=stats,
            limitations=limitations,
            provider=provider_name,
            model=model_name,
            prompt_version=PROMPT_VERSION,
            algorithm_version=ALGORITHM_VERSION,
            ai_enriched=ai_enriched,
        )
        self.db.add(run)
        await self.db.flush()

        for draft in drafts:
            self.db.add(
                SeoInternalLinkOpportunity(
                    organization_id=organization_id,
                    run_id=run.id,
                    generated_content_id=content.id,
                    content_brief_id=brief.id,
                    source_url=draft.source_url,
                    target_url=draft.target_url,
                    source_crawl_page_id=draft.source_crawl_page_id,
                    target_crawl_page_id=draft.target_crawl_page_id,
                    anchor_text=draft.anchor_text[:255],
                    anchor_alternatives=draft.anchor_alternatives,
                    opportunity_type=draft.opportunity_type,
                    relationship_reason=draft.relationship_reason,
                    source_topic=draft.source_topic,
                    target_topic=draft.target_topic,
                    source_keywords=draft.source_keywords,
                    target_keywords=draft.target_keywords,
                    relevance_score=draft.relevance_score,
                    confidence=draft.confidence,
                    score_breakdown=draft.score_breakdown,
                    evidence_refs=draft.evidence_refs,
                    limitations=draft.limitations,
                    status=draft.status,
                    dedupe_key=draft.dedupe_key(),
                )
            )

        await write_audit(
            self.db,
            action="seo_internal_links.generate",
            organization_id=organization_id,
            user_id=user_id,
            resource_type="seo_internal_link_run",
            resource_id=str(run.id),
            details={"content_id": str(content_id), "analysis_key": analysis_key, "opportunity_count": len(drafts)},
        )
        await self.db.flush()

        return {
            "run_id": run.id,
            "content_id": content_id,
            "content_brief_id": brief.id,
            "algorithm_version": ALGORITHM_VERSION,
            "prompt_version": PROMPT_VERSION,
            "opportunity_count": len(drafts),
            "ai_enriched": ai_enriched,
            "stats": stats,
        }

    async def get_report(self, *, organization_id: UUID, content_id: UUID) -> SeoInternalLinkReportOut:
        content = await SeoGeneratedContentService(self.db).get_content(
            organization_id=organization_id, content_id=content_id
        )
        run = await self.db.scalar(
            select(SeoInternalLinkRun)
            .where(
                SeoInternalLinkRun.organization_id == organization_id,
                SeoInternalLinkRun.generated_content_id == content.id,
            )
            .order_by(SeoInternalLinkRun.created_at.desc())
            .limit(1)
        )
        if not run:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="internal_links_not_found")

        opportunities = await self.list_opportunities(organization_id=organization_id, content_id=content_id, run_id=run.id)
        return SeoInternalLinkReportOut(
            run=SeoInternalLinkRunOut.model_validate(run),
            opportunities=opportunities,
            disclaimer=DISCLAIMER,
        )

    async def list_opportunities(
        self,
        *,
        organization_id: UUID,
        content_id: UUID,
        run_id: UUID | None = None,
        source_url: str | None = None,
        target_url: str | None = None,
        opportunity_type: str | None = None,
        status_filter: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SeoInternalLinkOpportunityOut]:
        await SeoGeneratedContentService(self.db).get_content(
            organization_id=organization_id, content_id=content_id
        )
        q = select(SeoInternalLinkOpportunity).where(
            SeoInternalLinkOpportunity.organization_id == organization_id,
            SeoInternalLinkOpportunity.generated_content_id == content_id,
        )
        if run_id:
            q = q.where(SeoInternalLinkOpportunity.run_id == run_id)
        if source_url:
            q = q.where(SeoInternalLinkOpportunity.source_url == source_url)
        if target_url:
            q = q.where(SeoInternalLinkOpportunity.target_url == target_url)
        if opportunity_type:
            q = q.where(SeoInternalLinkOpportunity.opportunity_type == opportunity_type)
        if status_filter:
            q = q.where(SeoInternalLinkOpportunity.status == status_filter)
        q = q.order_by(SeoInternalLinkOpportunity.relevance_score.desc()).offset(offset).limit(min(limit, 500))
        rows = list((await self.db.execute(q)).scalars().all())
        return [SeoInternalLinkOpportunityOut.model_validate(r) for r in rows]

    async def get_opportunity(
        self, *, organization_id: UUID, content_id: UUID, opportunity_id: UUID
    ) -> SeoInternalLinkOpportunityOut:
        await SeoGeneratedContentService(self.db).get_content(
            organization_id=organization_id, content_id=content_id
        )
        row = await self.db.scalar(
            select(SeoInternalLinkOpportunity).where(
                SeoInternalLinkOpportunity.id == opportunity_id,
                SeoInternalLinkOpportunity.organization_id == organization_id,
                SeoInternalLinkOpportunity.generated_content_id == content_id,
            ).limit(1)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="opportunity_not_found")
        return SeoInternalLinkOpportunityOut.model_validate(row)

    async def summary(self, *, organization_id: UUID, content_id: UUID) -> SeoInternalLinkSummaryOut:
        await SeoGeneratedContentService(self.db).get_content(
            organization_id=organization_id, content_id=content_id
        )
        rows = list(
            (
                await self.db.execute(
                    select(SeoInternalLinkOpportunity).where(
                        SeoInternalLinkOpportunity.organization_id == organization_id,
                        SeoInternalLinkOpportunity.generated_content_id == content_id,
                    )
                )
            ).scalars().all()
        )
        by_type = dict(Counter(r.opportunity_type for r in rows))
        by_confidence = dict(Counter(r.confidence for r in rows))
        return SeoInternalLinkSummaryOut(
            total=len(rows),
            by_type=by_type,
            by_confidence=by_confidence,
            disclaimer=DISCLAIMER,
        )


def _analysis_key(*, organization_id: UUID, content: SeoGeneratedContent, brief: SeoContentBrief) -> str:
    raw = f"{organization_id}|{content.id}|{content.generation_key}|{brief.generation_key}|{ALGORITHM_VERSION}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def _stats_from_drafts(drafts: list[InternalLinkOpportunityDraft]) -> dict:
    return {
        "total": len(drafts),
        "by_type": dict(Counter(d.opportunity_type for d in drafts)),
        "by_confidence": dict(Counter(d.confidence for d in drafts)),
        "avg_relevance_score": round(sum(d.relevance_score for d in drafts) / len(drafts), 4) if drafts else 0.0,
    }


def _content_summary(content: SeoGeneratedContent) -> str:
    return json.dumps(
        {
            "title": content.title,
            "target_url": content.target_url,
            "primary_keyword": content.primary_keyword,
            "target_topic": content.target_topic,
            "content_excerpt": (content.content or "")[:1500],
        },
        ensure_ascii=False,
    )


def _brief_summary(brief: SeoContentBrief) -> str:
    return json.dumps(
        {
            "title": brief.title,
            "primary_keyword": brief.primary_keyword,
            "target_topic": brief.target_topic,
            "internal_link_targets": brief.internal_link_targets,
        },
        ensure_ascii=False,
    )
