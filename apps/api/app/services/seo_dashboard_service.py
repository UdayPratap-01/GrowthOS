"""SEO dashboard aggregation service (M9.14)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.action_types import SEO_ACTIONS
from app.models.automation import AIAction
from app.models.enums import AIActionStatus, SearchConsoleSyncStatus, SeoCrawlStatus
from app.models.keyword_opportunity import KeywordOpportunity
from app.models.search_console import SearchConsoleOpportunity, SearchConsoleSync
from app.models.seo import SeoCrawl, SeoFinding
from app.models.seo_competitor import ContentGap, SeoCompetitor, SeoCompetitorCrawl
from app.models.seo_content_brief import SeoContentBrief
from app.models.seo_generated_content import SeoGeneratedContent
from app.models.seo_internal_link import SeoInternalLinkOpportunity
from app.models.seo_onpage_optimization import SeoOnPageFinding, SeoOnPageOptimizationRun
from app.models.seo_recommendation import SeoRecommendation
from app.models.seo_schema import SeoSchemaArtifact, SeoSchemaFinding
from app.models.topic_cluster import TopicCluster
from app.schemas.seo_dashboard import (
    DashboardActionsOut,
    DashboardAttentionItemOut,
    DashboardAttentionOut,
    DashboardCompetitorsOut,
    DashboardContentOut,
    DashboardInternalLinksOut,
    DashboardKeywordsOut,
    DashboardOnPageOut,
    DashboardOverviewOut,
    DashboardSchemaOut,
    DashboardSearchConsoleOut,
    DashboardTechnicalOut,
    DashboardTopicsOut,
    SeoDashboardOut,
)
from app.services.content_gap_service import ContentGapService
from app.services.keyword_opportunity_service import KeywordOpportunityService
from app.services.search_console_intelligence_service import SearchConsoleIntelligenceService
from app.services.seo_action_service import SeoActionService
from app.services.seo_analysis_service import SeoAnalysisService
from app.services.seo_recommendation_service import SeoRecommendationService
from app.services.topic_clustering_service import TopicClusteringService

DISCLAIMER = (
    "SEO dashboard aggregates persisted GrowthOS data only. "
    "It does not trigger crawls, syncs, AI generation, or autonomous actions. "
    "Metrics reflect available data — not synthetic SEO scores."
)

ATTENTION_LIMIT = 20
RECENT_ACTIONS_LIMIT = 5


class SeoDashboardService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_dashboard(self, *, organization_id: UUID) -> SeoDashboardOut:
        now = datetime.now(timezone.utc)
        latest_crawl = await self._latest_crawl(organization_id)
        latest_sync = await self._latest_sync(organization_id)

        technical = await self._technical_panel(organization_id, latest_crawl)
        search_console = await self._search_console_panel(organization_id)
        keywords = await self._keywords_panel(organization_id, latest_sync)
        topics = await self._topics_panel(organization_id, latest_sync)
        competitors = await self._competitors_panel(organization_id, latest_sync)
        content = await self._content_panel(organization_id)
        on_page = await self._on_page_panel(organization_id)
        schema = await self._schema_panel(organization_id)
        internal_links = await self._internal_links_panel(organization_id)
        actions = await self._actions_panel(organization_id)

        overview = DashboardOverviewOut(
            latest_crawl_id=latest_crawl.id if latest_crawl else None,
            latest_crawl_status=str(latest_crawl.status.value if hasattr(latest_crawl.status, "value") else latest_crawl.status)
            if latest_crawl
            else None,
            pages_crawled=int((latest_crawl.stats or {}).get("pages_crawled", 0)) if latest_crawl else 0,
            technical_findings=technical.total_findings,
            critical_high_findings=technical.by_severity.get("HIGH", 0),
            keyword_opportunities=keywords.total_opportunities,
            topic_clusters=topics.total_topics,
            content_gaps=competitors.total_gaps,
            recommendations=await self._count_recommendations(organization_id, latest_sync),
            content_briefs=content.briefs_count,
            generated_content=content.generated_count,
            internal_link_opportunities=internal_links.total_opportunities,
            schema_artifacts=schema.artifact_count,
            pending_actions=actions.pending,
        )

        attention = await self._attention_panel(
            organization_id,
            technical=technical,
            keywords=keywords,
            actions=actions,
            internal_links=internal_links,
            schema_panel=schema,
            latest_crawl=latest_crawl,
        )

        return SeoDashboardOut(
            generated_at=now,
            disclaimer=DISCLAIMER,
            overview=overview,
            technical=technical,
            search_console=search_console,
            keywords=keywords,
            topics=topics,
            competitors=competitors,
            content=content,
            on_page=on_page,
            schema_panel=schema,
            internal_links=internal_links,
            actions=actions,
            attention=attention,
        )

    async def _latest_crawl(self, organization_id: UUID) -> SeoCrawl | None:
        return await self.db.scalar(
            select(SeoCrawl)
            .where(
                SeoCrawl.organization_id == organization_id,
                SeoCrawl.status == SeoCrawlStatus.completed,
            )
            .order_by(SeoCrawl.completed_at.desc())
            .limit(1)
        )

    async def _latest_sync(self, organization_id: UUID) -> SearchConsoleSync | None:
        return await self.db.scalar(
            select(SearchConsoleSync)
            .where(
                SearchConsoleSync.organization_id == organization_id,
                SearchConsoleSync.status == SearchConsoleSyncStatus.completed,
            )
            .order_by(SearchConsoleSync.completed_at.desc())
            .limit(1)
        )

    async def _technical_panel(self, organization_id: UUID, crawl: SeoCrawl | None) -> DashboardTechnicalOut:
        if not crawl:
            return DashboardTechnicalOut(
                available=False,
                empty_message="No completed crawl yet. Run a crawl to populate technical SEO insights.",
            )
        summary = await SeoAnalysisService(self.db).summary(
            organization_id=organization_id, crawl_id=crawl.id
        )
        return DashboardTechnicalOut(
            available=True,
            crawl_id=crawl.id,
            root_url=crawl.root_url,
            total_findings=summary["total_findings"],
            by_severity=summary["by_severity"],
            by_category=summary["by_category"],
            affected_pages=summary["affected_pages"],
            analysis_status=summary.get("analysis_status"),
        )

    async def _search_console_panel(self, organization_id: UUID) -> DashboardSearchConsoleOut:
        try:
            summary = await SearchConsoleIntelligenceService(self.db).summary(
                organization_id=organization_id
            )
        except Exception:
            return DashboardSearchConsoleOut(
                available=False,
                empty_message="No Search Console sync data. Connect and sync in Search Console.",
            )
        totals = summary.get("totals") or {}
        last_sync = summary.get("last_sync")
        return DashboardSearchConsoleOut(
            available=True,
            site_url=summary.get("site_url"),
            connected=bool(summary.get("connected")),
            clicks=totals.get("clicks"),
            impressions=totals.get("impressions"),
            ctr=totals.get("ctr"),
            opportunity_count=int(summary.get("opportunity_count") or 0),
            by_priority=summary.get("by_priority") or {},
            last_sync_status=last_sync.get("status") if isinstance(last_sync, dict) else None,
        )

    async def _keywords_panel(
        self, organization_id: UUID, sync: SearchConsoleSync | None
    ) -> DashboardKeywordsOut:
        if not sync:
            return DashboardKeywordsOut(
                available=False,
                empty_message="Keyword opportunities require a completed Search Console sync.",
            )
        try:
            summary = await KeywordOpportunityService(self.db).summary(
                organization_id=organization_id, sync_id=sync.id
            )
        except Exception:
            return DashboardKeywordsOut(
                available=False,
                empty_message="No keyword opportunity data for the latest sync.",
            )
        by_priority = summary.get("by_priority") or {}
        return DashboardKeywordsOut(
            available=True,
            sync_id=sync.id,
            total_opportunities=int(summary.get("total_opportunities") or 0),
            unique_queries=int(summary.get("unique_queries") or 0),
            by_priority=by_priority,
            high_priority_count=int(by_priority.get("HIGH", 0) + by_priority.get("CRITICAL", 0)),
        )

    async def _topics_panel(
        self, organization_id: UUID, sync: SearchConsoleSync | None
    ) -> DashboardTopicsOut:
        if not sync:
            return DashboardTopicsOut(
                available=False,
                empty_message="Topic clusters require a completed Search Console sync.",
            )
        try:
            summary = await TopicClusteringService(self.db).summary(
                organization_id=organization_id, sync_id=sync.id
            )
        except Exception:
            return DashboardTopicsOut(
                available=False,
                empty_message="No topic cluster data for the latest sync.",
            )
        return DashboardTopicsOut(
            available=True,
            sync_id=sync.id,
            total_topics=int(summary.get("total_topics") or 0),
            total_clustered_queries=int(summary.get("total_clustered_queries") or 0),
            singleton_topics=int(summary.get("singleton_topics") or 0),
        )

    async def _competitors_panel(
        self, organization_id: UUID, sync: SearchConsoleSync | None
    ) -> DashboardCompetitorsOut:
        active = await self.db.scalar(
            select(func.count()).select_from(SeoCompetitor).where(
                SeoCompetitor.organization_id == organization_id,
                SeoCompetitor.status == "active",
            )
        )
        completed_crawls = await self.db.scalar(
            select(func.count()).select_from(SeoCompetitorCrawl).where(
                SeoCompetitorCrawl.organization_id == organization_id,
                SeoCompetitorCrawl.status == SeoCrawlStatus.completed,
            )
        )
        if not sync:
            return DashboardCompetitorsOut(
                available=bool(active),
                active_competitors=int(active or 0),
                completed_crawls=int(completed_crawls or 0),
                empty_message="Content gap analysis requires Search Console sync data."
                if not active
                else "Add competitors and run gap analysis after Search Console sync.",
            )
        try:
            summary = await ContentGapService(self.db).summary(
                organization_id=organization_id, sync_id=sync.id
            )
        except Exception:
            return DashboardCompetitorsOut(
                available=bool(active),
                active_competitors=int(active or 0),
                completed_crawls=int(completed_crawls or 0),
                empty_message="No content gap analysis for the latest sync.",
            )
        return DashboardCompetitorsOut(
            available=True,
            active_competitors=int(summary.get("active_competitors") or 0),
            completed_crawls=int(completed_crawls or 0),
            total_gaps=int(summary.get("total_gaps") or 0),
            by_gap_type=summary.get("by_gap_type") or {},
            has_competitor_data=bool(summary.get("has_competitor_data")),
        )

    async def _content_panel(self, organization_id: UUID) -> DashboardContentOut:
        briefs = (
            await self.db.execute(
                select(SeoContentBrief.status).where(SeoContentBrief.organization_id == organization_id)
            )
        ).scalars().all()
        generated = (
            await self.db.execute(
                select(SeoGeneratedContent.status).where(
                    SeoGeneratedContent.organization_id == organization_id
                )
            )
        ).scalars().all()
        opt_runs = await self.db.scalar(
            select(func.count(func.distinct(SeoOnPageOptimizationRun.generated_content_id))).select_from(
                SeoOnPageOptimizationRun
            ).where(SeoOnPageOptimizationRun.organization_id == organization_id)
        )
        schema_content = await self.db.scalar(
            select(func.count(func.distinct(SeoSchemaArtifact.generated_content_id))).select_from(
                SeoSchemaArtifact
            ).where(SeoSchemaArtifact.organization_id == organization_id)
        )
        link_content = await self.db.scalar(
            select(func.count(func.distinct(SeoInternalLinkOpportunity.generated_content_id))).select_from(
                SeoInternalLinkOpportunity
            ).where(SeoInternalLinkOpportunity.organization_id == organization_id)
        )
        brief_counter = Counter(briefs)
        gen_counter = Counter(generated)
        return DashboardContentOut(
            available=bool(briefs or generated),
            briefs_count=len(briefs),
            briefs_draft=int(brief_counter.get("draft", 0)),
            generated_count=len(generated),
            generated_draft=int(gen_counter.get("draft", 0)),
            content_with_optimization=int(opt_runs or 0),
            content_with_schema=int(schema_content or 0),
            content_with_internal_links=int(link_content or 0),
            empty_message="No content briefs or generated content yet." if not briefs and not generated else None,
        )

    async def _on_page_panel(self, organization_id: UUID) -> DashboardOnPageOut:
        runs = await self.db.scalar(
            select(func.count()).select_from(SeoOnPageOptimizationRun).where(
                SeoOnPageOptimizationRun.organization_id == organization_id
            )
        )
        findings = (
            await self.db.execute(
                select(SeoOnPageFinding.severity).where(
                    SeoOnPageFinding.organization_id == organization_id
                )
            )
        ).scalars().all()
        if not runs and not findings:
            return DashboardOnPageOut(
                available=False,
                empty_message="No on-page optimization runs yet.",
            )
        return DashboardOnPageOut(
            available=True,
            optimization_runs=int(runs or 0),
            total_findings=len(findings),
            by_severity=dict(Counter(str(s.value if hasattr(s, "value") else s) for s in findings)),
        )

    async def _schema_panel(self, organization_id: UUID) -> DashboardSchemaOut:
        artifacts = (
            await self.db.execute(
                select(
                    SeoSchemaArtifact.validation_status,
                    SeoSchemaArtifact.eligibility_status,
                ).where(SeoSchemaArtifact.organization_id == organization_id)
            )
        ).all()
        findings = await self.db.scalar(
            select(func.count()).select_from(SeoSchemaFinding).where(
                SeoSchemaFinding.organization_id == organization_id
            )
        )
        if not artifacts and not findings:
            return DashboardSchemaOut(
                available=False,
                empty_message="No schema artifacts generated yet.",
            )
        val_counter: Counter[str] = Counter()
        elig_counter: Counter[str] = Counter()
        for val, elig in artifacts:
            val_counter[str(val)] += 1
            elig_counter[str(elig)] += 1
        return DashboardSchemaOut(
            available=True,
            artifact_count=len(artifacts),
            by_validation_status=dict(val_counter),
            by_eligibility_status=dict(elig_counter),
            finding_count=int(findings or 0),
        )

    async def _internal_links_panel(self, organization_id: UUID) -> DashboardInternalLinksOut:
        rows = (
            await self.db.execute(
                select(
                    SeoInternalLinkOpportunity.status,
                    SeoInternalLinkOpportunity.confidence,
                    SeoInternalLinkOpportunity.opportunity_type,
                ).where(SeoInternalLinkOpportunity.organization_id == organization_id)
            )
        ).all()
        if not rows:
            return DashboardInternalLinksOut(
                available=False,
                empty_message="No internal-link opportunities yet. Generate from SEO Content.",
            )
        by_type = Counter(r[2] for r in rows)
        high_conf = sum(1 for r in rows if r[1] == "high")
        suggested = sum(1 for r in rows if r[0] == "suggested")
        orphan = int(by_type.get("orphan_page", 0))
        return DashboardInternalLinksOut(
            available=True,
            total_opportunities=len(rows),
            suggested_count=suggested,
            high_confidence_count=high_conf,
            by_type=dict(by_type),
            orphan_page_count=orphan,
        )

    async def _actions_panel(self, organization_id: UUID) -> DashboardActionsOut:
        summary = await SeoActionService(self.db).summary(organization_id)
        pending_rows = (
            await self.db.execute(
                select(AIAction)
                .where(
                    AIAction.organization_id == organization_id,
                    AIAction.action_type.in_(list(SEO_ACTIONS)),
                    AIAction.status == AIActionStatus.pending,
                )
                .order_by(AIAction.created_at.desc())
                .limit(RECENT_ACTIONS_LIMIT)
            )
        ).scalars().all()
        recent = [
            {
                "id": str(r.id),
                "action_type": str(r.action_type.value if hasattr(r.action_type, "value") else r.action_type),
                "status": str(r.status.value if hasattr(r.status, "value") else r.status),
                "description": r.description[:200],
                "capability": (r.payload or {}).get("capability", "review_only"),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in pending_rows
        ]
        return DashboardActionsOut(
            available=True,
            pending=summary.pending,
            approved=summary.approved,
            completed=summary.completed,
            rejected=summary.rejected,
            failed=summary.failed,
            review_only=summary.review_only,
            recent_pending=recent,
        )

    async def _count_recommendations(
        self, organization_id: UUID, sync: SearchConsoleSync | None
    ) -> int:
        if not sync:
            return 0
        try:
            summary = await SeoRecommendationService(self.db).summary(
                organization_id=organization_id, sync_id=sync.id
            )
            return int(summary.get("total_recommendations") or 0)
        except Exception:
            return 0

    async def _attention_panel(
        self,
        organization_id: UUID,
        *,
        technical: DashboardTechnicalOut,
        keywords: DashboardKeywordsOut,
        actions: DashboardActionsOut,
        internal_links: DashboardInternalLinksOut,
        schema_panel: DashboardSchemaOut,
        latest_crawl: SeoCrawl | None,
    ) -> DashboardAttentionOut:
        items: list[DashboardAttentionItemOut] = []

        if latest_crawl and technical.available:
            high_findings = (
                await self.db.execute(
                    select(SeoFinding)
                    .where(
                        SeoFinding.organization_id == organization_id,
                        SeoFinding.crawl_id == latest_crawl.id,
                        SeoFinding.severity.in_(["HIGH", "high"]),
                    )
                    .order_by(SeoFinding.created_at.desc())
                    .limit(5)
                )
            ).scalars().all()
            for f in high_findings:
                sev = str(f.severity.value if hasattr(f.severity, "value") else f.severity)
                items.append(
                    DashboardAttentionItemOut(
                        source="technical_seo",
                        item_type="finding",
                        title=f.title,
                        reason=f"Critical/high severity technical finding ({f.category})",
                        severity_or_priority=sev,
                        reference_id=str(f.id),
                        link_path="/seo/crawler",
                    )
                )

        if keywords.available and keywords.high_priority_count > 0:
            items.append(
                DashboardAttentionItemOut(
                    source="keywords",
                    item_type="summary",
                    title=f"{keywords.high_priority_count} high-priority keyword opportunities",
                    reason="Keyword opportunities marked HIGH or CRITICAL priority",
                    severity_or_priority="HIGH",
                    link_path="/seo/keywords",
                )
            )

        if actions.pending > 0:
            for r in actions.recent_pending[:5]:
                items.append(
                    DashboardAttentionItemOut(
                        source="seo_actions",
                        item_type="action",
                        title=r["description"],
                        reason="SEO action pending explicit approval",
                        severity_or_priority="PENDING",
                        reference_id=r["id"],
                        link_path="/seo/content",
                    )
                )

        if internal_links.available and internal_links.high_confidence_count > 0:
            items.append(
                DashboardAttentionItemOut(
                    source="internal_links",
                    item_type="summary",
                    title=f"{internal_links.high_confidence_count} high-confidence internal-link opportunities",
                    reason="Internal links with high relevance confidence from M9.12",
                    severity_or_priority="HIGH",
                    link_path="/seo/content",
                )
            )

        if schema_panel.available:
            invalid = schema_panel.by_validation_status.get("invalid", 0)
            if invalid:
                items.append(
                    DashboardAttentionItemOut(
                        source="schema",
                        item_type="summary",
                        title=f"{invalid} schema artifacts with validation issues",
                        reason="Schema validation reported invalid JSON-LD",
                        severity_or_priority="HIGH",
                        link_path="/seo/content",
                    )
                )

        gsc_high = await self._gsc_high_priority_count(organization_id)
        if gsc_high:
            items.append(
                DashboardAttentionItemOut(
                    source="search_console",
                    item_type="summary",
                    title=f"{gsc_high} high-priority Search Console opportunities",
                    reason="Search Console intelligence flagged high-priority items",
                    severity_or_priority="HIGH",
                    link_path="/seo/search-console",
                )
            )

        rec_high = await self._recommendation_high_count(organization_id)
        if rec_high:
            items.append(
                DashboardAttentionItemOut(
                    source="recommendations",
                    item_type="summary",
                    title=f"{rec_high} high-priority SEO recommendations",
                    reason="AI SEO recommendations marked high priority",
                    severity_or_priority="HIGH",
                    link_path="/seo/recommendations",
                )
            )

        return DashboardAttentionOut(
            available=bool(items),
            items=items[:ATTENTION_LIMIT],
            empty_message="No items currently flagged for attention." if not items else None,
        )

    async def _gsc_high_priority_count(self, organization_id: UUID) -> int:
        sync = await self._latest_sync(organization_id)
        if not sync:
            return 0
        return int(
            await self.db.scalar(
                select(func.count()).select_from(SearchConsoleOpportunity).where(
                    SearchConsoleOpportunity.organization_id == organization_id,
                    SearchConsoleOpportunity.sync_id == sync.id,
                    SearchConsoleOpportunity.priority.in_(["HIGH", "CRITICAL", "high", "critical"]),
                )
            )
            or 0
        )

    async def _recommendation_high_count(self, organization_id: UUID) -> int:
        sync = await self._latest_sync(organization_id)
        if not sync:
            return 0
        return int(
            await self.db.scalar(
                select(func.count()).select_from(SeoRecommendation).where(
                    SeoRecommendation.organization_id == organization_id,
                    SeoRecommendation.sync_id == sync.id,
                    SeoRecommendation.priority.in_(["HIGH", "CRITICAL", "high", "critical"]),
                )
            )
            or 0
        )
