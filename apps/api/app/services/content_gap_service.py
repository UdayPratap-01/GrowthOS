"""Content-gap analysis over user topics and competitor crawl observations."""

from __future__ import annotations

from collections import Counter
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.enums import SearchConsoleSyncStatus, SeoCrawlStatus
from app.models.keyword_opportunity import KeywordOpportunity
from app.models.search_console import SearchConsoleSync
from app.models.seo_competitor import ContentGap, ContentGapAnalysisRun, SeoCompetitor, SeoCompetitorCrawl, SeoCompetitorPage
from app.models.topic_cluster import TopicCluster, TopicClusterQuery
from app.seo.content_gap.engine import GapAnalysisInput, build_user_topics_for_matching, detect_content_gaps
from app.seo.content_gap.thresholds import ALGORITHM_VERSION, DEFAULT_GAP_THRESHOLDS

DISCLAIMER = (
    "Content-gap signals compare user Search Console topic clusters with observed competitor page content. "
    "Competitor crawls do not provide rankings, traffic, search volume, or backlinks. "
    "Signals are deterministic lexical matches — not AI recommendations."
)

ALLOWED_SORT = {
    "similarity": ContentGap.similarity,
    "created_at": ContentGap.created_at,
    "gap_type": ContentGap.gap_type,
}


class ContentGapService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def analyze(self, *, organization_id: UUID, sync_id: UUID | None = None) -> dict:
        sync = await self._resolve_sync(organization_id, sync_id)
        topics = (
            await self.db.execute(
                select(TopicCluster).where(
                    TopicCluster.sync_id == sync.id,
                    TopicCluster.organization_id == organization_id,
                    TopicCluster.algorithm_version == "v1",
                )
            )
        ).scalars().all()
        if not topics:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No topic clusters found — run topic analysis first")

        competitors = (
            await self.db.execute(
                select(SeoCompetitor).where(
                    SeoCompetitor.organization_id == organization_id,
                    SeoCompetitor.status == "active",
                )
            )
        ).scalars().all()
        if not competitors:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="insufficient_competitor_data")

        competitor_pages = await self._load_competitor_pages(organization_id, [c.id for c in competitors])
        if not competitor_pages:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="insufficient_competitor_data")

        queries = (
            await self.db.execute(
                select(TopicClusterQuery).where(
                    TopicClusterQuery.organization_id == organization_id,
                    TopicClusterQuery.topic_cluster_id.in_([t.id for t in topics]),
                )
            )
        ).scalars().all()
        queries_by_cluster: dict[str, list[str]] = {}
        for q in queries:
            queries_by_cluster.setdefault(str(q.topic_cluster_id), []).append(q.query)

        opportunities = (
            await self.db.execute(
                select(KeywordOpportunity).where(
                    KeywordOpportunity.sync_id == sync.id,
                    KeywordOpportunity.organization_id == organization_id,
                )
            )
        ).scalars().all()

        settings = get_settings()
        thresholds = DEFAULT_GAP_THRESHOLDS.__class__(
            max_comparisons=min(settings.content_gap_max_comparisons, DEFAULT_GAP_THRESHOLDS.max_comparisons)
        )
        user_topics = build_user_topics_for_matching(topics, queries_by_cluster)
        drafts = detect_content_gaps(
            GapAnalysisInput(
                user_topics=user_topics,
                opportunities=[
                    {
                        "query": o.query,
                        "normalized_query": o.normalized_query,
                        "opportunity_type": o.opportunity_type,
                        "impressions": o.impressions,
                        "clicks": o.clicks,
                        "page_url": o.page_url,
                    }
                    for o in opportunities
                ],
                competitor_pages=competitor_pages,
                thresholds=thresholds,
            )
        )

        await self.db.execute(
            delete(ContentGap).where(
                ContentGap.organization_id == organization_id,
                ContentGap.sync_id == sync.id,
                ContentGap.algorithm_version == ALGORITHM_VERSION,
            )
        )
        run = ContentGapAnalysisRun(
            organization_id=organization_id,
            sync_id=sync.id,
            status="completed",
            algorithm_version=ALGORITHM_VERSION,
            stats={"gaps_created": len(drafts), "competitor_pages": len(competitor_pages), "user_topics": len(topics)},
        )
        self.db.add(run)
        await self.db.flush()

        for draft in drafts:
            self.db.add(
                ContentGap(
                    organization_id=organization_id,
                    analysis_run_id=run.id,
                    sync_id=sync.id,
                    competitor_id=UUID(draft.competitor_id) if draft.competitor_id else None,
                    competitor_crawl_id=UUID(draft.competitor_crawl_id) if draft.competitor_crawl_id else None,
                    competitor_page_id=UUID(draft.competitor_page_id) if draft.competitor_page_id else None,
                    competitor_url=draft.competitor_url,
                    gap_type=draft.gap_type,
                    topic_label=draft.topic_label[:512],
                    user_topic_id=UUID(draft.user_topic_id) if draft.user_topic_id else None,
                    user_query=draft.user_query,
                    competitor_title=draft.competitor_title,
                    competitor_h1=draft.competitor_h1,
                    similarity=draft.similarity,
                    match_strength=draft.match_strength,
                    evidence=draft.evidence,
                    explanation=draft.explanation,
                    status="open",
                    algorithm_version=ALGORITHM_VERSION,
                    dedupe_key=draft.dedupe_key[:512],
                )
            )
        await self.db.flush()
        return {
            "analysis_run_id": run.id,
            "sync_id": sync.id,
            "gaps_created": len(drafts),
            "algorithm_version": ALGORITHM_VERSION,
        }

    async def list_gaps(
        self,
        *,
        organization_id: UUID,
        sync_id: UUID | None = None,
        competitor_id: UUID | None = None,
        gap_type: str | None = None,
        status_filter: str | None = None,
        min_similarity: float | None = None,
        max_similarity: float | None = None,
        sort: str = "similarity",
        limit: int = 100,
        offset: int = 0,
    ) -> list[ContentGap]:
        sync = await self._resolve_sync(organization_id, sync_id)
        q = select(ContentGap).where(
            ContentGap.organization_id == organization_id,
            ContentGap.sync_id == sync.id,
            ContentGap.algorithm_version == ALGORITHM_VERSION,
        )
        if competitor_id:
            q = q.where(ContentGap.competitor_id == competitor_id)
        if gap_type:
            q = q.where(ContentGap.gap_type == gap_type)
        if status_filter:
            q = q.where(ContentGap.status == status_filter)
        if min_similarity is not None:
            q = q.where(ContentGap.similarity >= min_similarity)
        if max_similarity is not None:
            q = q.where(ContentGap.similarity <= max_similarity)
        sort_col = ALLOWED_SORT.get(sort, ContentGap.similarity)
        q = q.order_by(sort_col.desc()).offset(offset).limit(min(limit, 500))
        return list((await self.db.execute(q)).scalars().all())

    async def get_gap(self, *, organization_id: UUID, gap_id: UUID) -> ContentGap:
        row = await self.db.scalar(
            select(ContentGap).where(ContentGap.id == gap_id, ContentGap.organization_id == organization_id).limit(1)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content gap not found")
        return row

    async def summary(self, *, organization_id: UUID, sync_id: UUID | None = None) -> dict:
        sync = await self._resolve_sync(organization_id, sync_id)
        rows = (
            await self.db.execute(
                select(ContentGap).where(
                    ContentGap.organization_id == organization_id,
                    ContentGap.sync_id == sync.id,
                    ContentGap.algorithm_version == ALGORITHM_VERSION,
                )
            )
        ).scalars().all()
        competitors = await self.db.scalar(
            select(func.count()).select_from(SeoCompetitor).where(
                SeoCompetitor.organization_id == organization_id,
                SeoCompetitor.status == "active",
            )
        )
        pages = await self._load_competitor_pages(organization_id, None)
        by_type = dict(Counter(r.gap_type for r in rows))
        by_strength = dict(Counter(r.match_strength for r in rows))
        return {
            "sync_id": sync.id,
            "algorithm_version": ALGORITHM_VERSION,
            "total_gaps": len(rows),
            "by_gap_type": by_type,
            "by_match_strength": by_strength,
            "active_competitors": int(competitors or 0),
            "competitor_pages_observed": len(pages),
            "has_competitor_data": bool(pages),
            "disclaimer": DISCLAIMER,
        }

    async def _load_competitor_pages(self, organization_id: UUID, competitor_ids: list[UUID] | None) -> list[dict]:
        q = (
            select(SeoCompetitorPage, SeoCompetitorCrawl.competitor_id, SeoCompetitorCrawl.id)
            .join(SeoCompetitorCrawl, SeoCompetitorPage.crawl_id == SeoCompetitorCrawl.id)
            .where(
                SeoCompetitorPage.organization_id == organization_id,
                SeoCompetitorCrawl.status == SeoCrawlStatus.completed,
            )
        )
        if competitor_ids:
            q = q.where(SeoCompetitorCrawl.competitor_id.in_(competitor_ids))
        rows = (await self.db.execute(q.order_by(SeoCompetitorPage.created_at.desc()))).all()
        out: list[dict] = []
        seen_urls: set[str] = set()
        for page, comp_id, crawl_id in rows:
            if page.url in seen_urls:
                continue
            seen_urls.add(page.url)
            out.append(
                {
                    "page_id": page.id,
                    "crawl_id": crawl_id,
                    "competitor_id": comp_id,
                    "url": page.url,
                    "observations": page.observations,
                    "fetched_at": page.fetched_at.isoformat() if page.fetched_at else None,
                }
            )
        return out[: get_settings().content_gap_max_comparisons]

    async def _resolve_sync(self, organization_id: UUID, sync_id: UUID | None) -> SearchConsoleSync:
        if sync_id:
            row = await self.db.scalar(
                select(SearchConsoleSync).where(
                    SearchConsoleSync.id == sync_id,
                    SearchConsoleSync.organization_id == organization_id,
                ).limit(1)
            )
            if not row:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sync not found")
            return row
        row = await self.db.scalar(
            select(SearchConsoleSync)
            .where(
                SearchConsoleSync.organization_id == organization_id,
                SearchConsoleSync.status == SearchConsoleSyncStatus.completed,
            )
            .order_by(SearchConsoleSync.completed_at.desc())
            .limit(1)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No completed Search Console sync found")
        return row
