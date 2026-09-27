"""SEO on-page optimizer service (M9.10)."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.seo_onpage_optimizer_agent import SeoOnPageOptimizerAgent, SeoOnPageOptimizerRequest
from app.ai.providers.base import AIGenerationError
from app.ai.providers.factory import AIProviderConfigurationError, get_ai_provider
from app.models.seo_content_brief import SeoContentBrief
from app.models.seo_onpage_optimization import SeoOnPageFinding, SeoOnPageOptimizationRun
from app.models.seo_generated_content import SeoGeneratedContent
from app.schemas.client import ClientContext
from app.schemas.seo_onpage_optimizer import SeoOnPageOptimizationOut, SeoOnPageFindingOut, SeoOnPageOptimizationRunOut
from app.security.audit import write_audit
from app.seo.onpage.engine import analyze_on_page
from app.seo.onpage.thresholds import ALGORITHM_VERSION, DEFAULT_ONPAGE_LIMITS, PROMPT_VERSION
from app.seo.onpage.types import OnPageFindingDraft
from app.services.seo_content_brief_service import SeoContentBriefService
from app.services.seo_generated_content_service import SeoGeneratedContentService

DISCLAIMER = (
    "On-page optimization findings are recommendations for a GrowthOS draft artifact. "
    "They do not modify live websites and must not be auto-published."
)

NO_PUBLISHING_NOTE = "M9.10 evaluates draft content only. It does not publish or modify live websites."


class SeoOnPageOptimizerService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def optimize(
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
        if brief.id != content.content_brief_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="brief_not_found")

        body_len = len(content.content or "")
        if body_len > 100_000:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="content_too_large")

        analysis_key = _analysis_key(
            organization_id=organization_id,
            content=content,
            brief=brief,
        )

        await self.db.execute(
            delete(SeoOnPageOptimizationRun).where(
                SeoOnPageOptimizationRun.organization_id == organization_id,
                SeoOnPageOptimizationRun.analysis_key == analysis_key,
            )
        )

        drafts = analyze_on_page(content, brief)
        drafts = drafts[: DEFAULT_ONPAGE_LIMITS.max_findings]

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
                agent = SeoOnPageOptimizerAgent(provider)
                findings_payload = [
                    {
                        "finding_type": d.finding_type,
                        "category": d.category,
                        "title": d.title,
                        "summary": d.summary,
                        "current_value": d.current_value,
                        "recommendation": d.recommendation,
                    }
                    for d in drafts[: DEFAULT_ONPAGE_LIMITS.max_ai_suggestions]
                ]
                ctx_json = json.dumps(findings_payload, ensure_ascii=False)
                if len(ctx_json) <= DEFAULT_ONPAGE_LIMITS.max_prompt_chars:
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
                        SeoOnPageOptimizerRequest(
                            findings_json=ctx_json,
                            content_summary=_content_summary(content),
                            brief_summary=_brief_summary(brief),
                        ),
                    )
                    suggestion_map = {s.finding_type: s for s in output.suggestions}
                    for draft in drafts:
                        if draft.finding_type in suggestion_map:
                            draft.suggested_change = suggestion_map[draft.finding_type].suggested_change
                    ai_limitations = list(output.limitations)
                    ai_enriched = bool(output.suggestions)
            except (AIProviderConfigurationError, AIGenerationError):
                ai_limitations.append("AI suggestions unavailable; deterministic findings only.")

        stats = _stats_from_drafts(drafts)
        limitations = [NO_PUBLISHING_NOTE, "Structured data validation is out of scope for M9.10 (M9.11)."]
        limitations.extend(ai_limitations)

        run = SeoOnPageOptimizationRun(
            organization_id=organization_id,
            generated_content_id=content.id,
            content_brief_id=brief.id,
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
                SeoOnPageFinding(
                    organization_id=organization_id,
                    run_id=run.id,
                    generated_content_id=content.id,
                    content_brief_id=brief.id,
                    finding_type=draft.finding_type,
                    category=draft.category,
                    severity=draft.severity,
                    priority=draft.priority,
                    status=draft.status,
                    title=draft.title[:255],
                    summary=draft.summary,
                    rationale=draft.rationale,
                    current_value=draft.current_value,
                    expected_value=draft.expected_value,
                    recommendation=draft.recommendation,
                    evidence_refs=draft.evidence_refs,
                    affected_section=draft.affected_section,
                    affected_element=draft.affected_element,
                    suggested_change=draft.suggested_change,
                    dedupe_key=draft.dedupe_key(),
                )
            )

        await write_audit(
            self.db,
            action="seo_onpage.optimize",
            organization_id=organization_id,
            user_id=user_id,
            resource_type="seo_onpage_optimization_run",
            resource_id=str(run.id),
            details={"content_id": str(content_id), "analysis_key": analysis_key, "finding_count": len(drafts)},
        )
        await self.db.flush()

        return {
            "run_id": run.id,
            "content_id": content_id,
            "content_brief_id": brief.id,
            "algorithm_version": ALGORITHM_VERSION,
            "prompt_version": PROMPT_VERSION,
            "finding_count": len(drafts),
            "ai_enriched": ai_enriched,
            "stats": stats,
        }

    async def get_optimization(
        self, *, organization_id: UUID, content_id: UUID
    ) -> SeoOnPageOptimizationOut:
        content = await SeoGeneratedContentService(self.db).get_content(
            organization_id=organization_id, content_id=content_id
        )
        run = await self.db.scalar(
            select(SeoOnPageOptimizationRun)
            .where(
                SeoOnPageOptimizationRun.organization_id == organization_id,
                SeoOnPageOptimizationRun.generated_content_id == content.id,
            )
            .order_by(SeoOnPageOptimizationRun.created_at.desc())
            .limit(1)
        )
        if not run:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="optimization_not_found")

        findings = await self.list_findings(organization_id=organization_id, content_id=content_id, run_id=run.id)
        return SeoOnPageOptimizationOut(
            run=SeoOnPageOptimizationRunOut.model_validate(run),
            findings=findings,
            disclaimer=DISCLAIMER,
        )

    async def list_findings(
        self,
        *,
        organization_id: UUID,
        content_id: UUID,
        run_id: UUID | None = None,
        category: str | None = None,
        severity: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SeoOnPageFindingOut]:
        await SeoGeneratedContentService(self.db).get_content(
            organization_id=organization_id, content_id=content_id
        )
        q = select(SeoOnPageFinding).where(
            SeoOnPageFinding.organization_id == organization_id,
            SeoOnPageFinding.generated_content_id == content_id,
        )
        if run_id:
            q = q.where(SeoOnPageFinding.run_id == run_id)
        if category:
            q = q.where(SeoOnPageFinding.category == category)
        if severity:
            q = q.where(SeoOnPageFinding.severity == severity)
        q = q.order_by(SeoOnPageFinding.created_at.desc()).offset(offset).limit(min(limit, 500))
        rows = list((await self.db.execute(q)).scalars().all())
        return [SeoOnPageFindingOut.model_validate(r) for r in rows]

    async def get_finding(
        self, *, organization_id: UUID, content_id: UUID, finding_id: UUID
    ) -> SeoOnPageFindingOut:
        await SeoGeneratedContentService(self.db).get_content(
            organization_id=organization_id, content_id=content_id
        )
        row = await self.db.scalar(
            select(SeoOnPageFinding).where(
                SeoOnPageFinding.id == finding_id,
                SeoOnPageFinding.organization_id == organization_id,
                SeoOnPageFinding.generated_content_id == content_id,
            ).limit(1)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="finding_not_found")
        return SeoOnPageFindingOut.model_validate(row)


def _analysis_key(*, organization_id: UUID, content: SeoGeneratedContent, brief: SeoContentBrief) -> str:
    raw = (
        f"{organization_id}|{content.id}|{content.generation_key}|{brief.generation_key}|{ALGORITHM_VERSION}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def _stats_from_drafts(drafts: list[OnPageFindingDraft]) -> dict:
    by_severity = dict(Counter(d.severity for d in drafts))
    by_category = dict(Counter(d.category for d in drafts))
    return {
        "total": len(drafts),
        "by_severity": by_severity,
        "by_category": by_category,
    }


def _content_summary(content: SeoGeneratedContent) -> str:
    return json.dumps(
        {
            "title": content.title,
            "meta_title": content.meta_title,
            "meta_description": content.meta_description,
            "primary_keyword": content.primary_keyword,
            "word_count": content.word_count,
            "content_excerpt": (content.content or "")[:2000],
        },
        ensure_ascii=False,
    )


def _brief_summary(brief: SeoContentBrief) -> str:
    return json.dumps(
        {
            "title": brief.title,
            "primary_keyword": brief.primary_keyword,
            "target_topic": brief.target_topic,
            "search_intent": brief.search_intent,
            "outline_headings": [o.get("heading") for o in (brief.outline or [])[:10]],
        },
        ensure_ascii=False,
    )
