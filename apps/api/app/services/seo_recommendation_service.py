"""SEO AI recommendation service — evidence snapshot, AI interpretation, grounding."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.seo_recommendation_agent import SeoRecommendationAgent, SeoRecommendationRequest
from app.ai.providers.base import AIGenerationError
from app.ai.providers.factory import AIProviderConfigurationError, get_ai_provider
from app.models.seo_recommendation import SeoRecommendation, SeoRecommendationRun
from app.schemas.client import ClientContext
from app.security.audit import write_audit
from app.seo.recommendations.evidence import build_evidence_snapshot
from app.seo.recommendations.grounding import validate_and_filter_recommendations
from app.seo.recommendations.thresholds import ALGORITHM_VERSION, DEFAULT_SEO_RECOMMENDATION_LIMITS, PROMPT_VERSION

DISCLAIMER = (
    "SEO recommendations are AI interpretations of deterministic GrowthOS evidence. "
    "They are not guaranteed outcomes. Competitor rankings, traffic, search volume, and backlinks are unavailable."
)

ALLOWED_SORT = {
    "confidence": SeoRecommendation.confidence,
    "created_at": SeoRecommendation.created_at,
    "priority": SeoRecommendation.priority,
}


class SeoRecommendationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def generate(
        self,
        *,
        organization_id: UUID,
        user_id: UUID | None = None,
        sync_id: UUID | None = None,
    ) -> dict:
        snapshot = await build_evidence_snapshot(self.db, organization_id=organization_id, sync_id=sync_id)
        if snapshot.total_records() == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="insufficient_evidence")

        evidence_json = json.dumps(snapshot.as_prompt_dict(), ensure_ascii=False)
        if len(evidence_json) > DEFAULT_SEO_RECOMMENDATION_LIMITS.max_prompt_chars:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="evidence_snapshot_too_large")

        generation_key = hashlib.sha256(
            f"{organization_id}|{snapshot.sync_id}|{snapshot.evidence_hash()}|{PROMPT_VERSION}|{ALGORITHM_VERSION}".encode()
        ).hexdigest()[:64]

        await self.db.execute(
            delete(SeoRecommendation).where(
                SeoRecommendation.organization_id == organization_id,
                SeoRecommendation.generation_key == generation_key,
            )
        )
        await self.db.execute(
            delete(SeoRecommendationRun).where(
                SeoRecommendationRun.organization_id == organization_id,
                SeoRecommendationRun.generation_key == generation_key,
            )
        )

        run = SeoRecommendationRun(
            organization_id=organization_id,
            sync_id=snapshot.sync_id,
            status="running",
            generation_key=generation_key,
            evidence_snapshot=snapshot.as_prompt_dict(),
            stats={"evidence_records": snapshot.total_records()},
            provider="unknown",
            model="unknown",
            prompt_version=PROMPT_VERSION,
            algorithm_version=ALGORITHM_VERSION,
        )
        self.db.add(run)
        await self.db.flush()

        try:
            provider = get_ai_provider()
            agent = SeoRecommendationAgent(provider)
            context = ClientContext(
                client_id=organization_id,
                organization_id=organization_id,
                business_name="SEO",
                industry=None,
                website=snapshot.site_url,
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
                context,
                SeoRecommendationRequest(evidence_json=evidence_json, site_url=snapshot.site_url),
            )
            run.provider = getattr(provider, "name", "unknown")
            inner = getattr(provider, "inner", provider)
            run.model = getattr(inner, "model", getattr(inner, "name", "unknown"))
        except AIProviderConfigurationError as exc:
            run.status = "failed"
            run.error = "ai_provider_unavailable"
            await self.db.flush()
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="ai_provider_unavailable") from exc
        except AIGenerationError as exc:
            run.status = "failed"
            run.error = str(exc)[:500]
            await self.db.flush()
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="ai_generation_failed") from exc

        valid = validate_and_filter_recommendations(output, snapshot)[: DEFAULT_SEO_RECOMMENDATION_LIMITS.max_recommendations_per_run]
        if not valid:
            run.status = "failed"
            run.error = "grounding_validation_failed"
            await self.db.flush()
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="grounding_validation_failed")

        created = 0
        for item in valid:
            dedupe_key = hashlib.sha256(
                f"{generation_key}|{item.type}|{item.title.casefold()}".encode()
            ).hexdigest()[:64]
            self.db.add(
                SeoRecommendation(
                    organization_id=organization_id,
                    run_id=run.id,
                    sync_id=snapshot.sync_id,
                    recommendation_type=item.type,
                    title=item.title[:255],
                    summary=item.summary,
                    rationale=item.rationale,
                    priority=item.priority,
                    impact=item.impact,
                    effort=item.effort,
                    confidence=item.confidence,
                    status="open",
                    evidence_refs=[r.model_dump() for r in item.evidence_refs],
                    affected_urls=item.affected_urls,
                    affected_keywords=item.affected_keywords,
                    affected_topics=item.affected_topics,
                    competitor_context=item.competitor_context,
                    recommended_action=item.recommended_action,
                    expected_outcome=item.expected_outcome,
                    limitations=item.limitations + output.data_limitations,
                    provider=run.provider,
                    model=run.model,
                    prompt_version=PROMPT_VERSION,
                    algorithm_version=ALGORITHM_VERSION,
                    generation_key=generation_key,
                    dedupe_key=dedupe_key,
                )
            )
            created += 1

        run.status = "completed"
        run.stats = {**run.stats, "recommendations_created": created, "rejected_count": len(output.recommendations) - created}
        await self.db.flush()

        await write_audit(
            self.db,
            action="seo_recommendation.generate",
            organization_id=organization_id,
            user_id=user_id,
            resource_type="seo_recommendation_run",
            resource_id=str(run.id),
            details={"created": created, "generation_key": generation_key},
        )
        await self.db.flush()

        return {
            "run_id": run.id,
            "sync_id": snapshot.sync_id,
            "recommendations_created": created,
            "algorithm_version": ALGORITHM_VERSION,
            "prompt_version": PROMPT_VERSION,
            "provider": run.provider,
        }

    async def list_recommendations(
        self,
        *,
        organization_id: UUID,
        sync_id: UUID | None = None,
        recommendation_type: str | None = None,
        priority: str | None = None,
        status_filter: str | None = None,
        sort: str = "confidence",
        limit: int = 100,
        offset: int = 0,
    ) -> list[SeoRecommendation]:
        q = select(SeoRecommendation).where(
            SeoRecommendation.organization_id == organization_id,
            SeoRecommendation.algorithm_version == ALGORITHM_VERSION,
        )
        if sync_id:
            q = q.where(SeoRecommendation.sync_id == sync_id)
        if recommendation_type:
            q = q.where(SeoRecommendation.recommendation_type == recommendation_type)
        if priority:
            q = q.where(SeoRecommendation.priority == priority)
        if status_filter:
            q = q.where(SeoRecommendation.status == status_filter)
        sort_col = ALLOWED_SORT.get(sort, SeoRecommendation.confidence)
        q = q.order_by(sort_col.desc()).offset(offset).limit(min(limit, 500))
        return list((await self.db.execute(q)).scalars().all())

    async def get_recommendation(self, *, organization_id: UUID, recommendation_id: UUID) -> SeoRecommendation:
        row = await self.db.scalar(
            select(SeoRecommendation).where(
                SeoRecommendation.id == recommendation_id,
                SeoRecommendation.organization_id == organization_id,
            ).limit(1)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SEO recommendation not found")
        return row

    async def summary(self, *, organization_id: UUID, sync_id: UUID | None = None) -> dict:
        q = select(SeoRecommendation).where(
            SeoRecommendation.organization_id == organization_id,
            SeoRecommendation.algorithm_version == ALGORITHM_VERSION,
        )
        if sync_id:
            q = q.where(SeoRecommendation.sync_id == sync_id)
        rows = (await self.db.execute(q)).scalars().all()
        return {
            "sync_id": sync_id,
            "total_recommendations": len(rows),
            "by_type": dict(Counter(r.recommendation_type for r in rows)),
            "by_priority": dict(Counter(r.priority for r in rows)),
            "algorithm_version": ALGORITHM_VERSION,
            "prompt_version": PROMPT_VERSION,
            "disclaimer": DISCLAIMER,
        }
