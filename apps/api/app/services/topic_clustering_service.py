"""Topic clustering service — deterministic grouping over M9.4 keyword data."""

from __future__ import annotations

from collections import Counter
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import SearchConsoleSyncStatus
from app.models.keyword_opportunity import KeywordOpportunity
from app.models.search_console import SearchConsolePerformanceRow, SearchConsoleSync
from app.models.topic_cluster import TopicCluster, TopicClusterPage, TopicClusterQuery
from app.seo.keywords.normalize import normalize_query
from app.seo.topics.cluster import build_query_records, cluster_queries
from app.seo.topics.similarity import jaccard_similarity
from app.seo.topics.thresholds import ALGORITHM_VERSION, DEFAULT_TOPIC_THRESHOLDS
from app.seo.topics.tokens import tokenize_query

DISCLAIMER = (
    "Topic clusters are derived from Search Console query performance and keyword opportunity data. "
    "Cluster labels are deterministic lexical summaries — not AI-generated topic names. "
    "Average position is a weighted aggregate, not an exact rank. "
    "Multi-page signals indicate multiple URLs received impressions for related queries."
)

ALLOWED_SORT = {
    "impressions": TopicCluster.total_impressions,
    "clicks": TopicCluster.total_clicks,
    "ctr": TopicCluster.aggregate_ctr,
    "average_position": TopicCluster.weighted_average_position,
    "query_count": TopicCluster.query_count,
    "page_count": TopicCluster.page_count,
    "opportunity_count": TopicCluster.opportunity_count,
    "created_at": TopicCluster.created_at,
}


class TopicClusteringService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def analyze(self, *, organization_id: UUID, sync_id: UUID | None = None) -> dict:
        sync = await self._resolve_sync(organization_id, sync_id)
        if sync.status != SearchConsoleSyncStatus.completed:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Search Console sync not completed")

        perf_rows = (
            await self.db.execute(
                select(SearchConsolePerformanceRow).where(
                    SearchConsolePerformanceRow.sync_id == sync.id,
                    SearchConsolePerformanceRow.organization_id == organization_id,
                    SearchConsolePerformanceRow.dimension_type.in_(["query", "query_page"]),
                )
            )
        ).scalars().all()

        opp_rows = (
            await self.db.execute(
                select(KeywordOpportunity).where(
                    KeywordOpportunity.sync_id == sync.id,
                    KeywordOpportunity.organization_id == organization_id,
                )
            )
        ).scalars().all()
        if not opp_rows:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No keyword opportunities found — run keyword analysis first",
            )

        opp_counts = Counter(o.normalized_query for o in opp_rows)
        input_rows: list[dict] = []
        for row in perf_rows:
            if not row.query:
                continue
            norm = normalize_query(row.query)
            if not norm:
                continue
            input_rows.append(
                {
                    "query": row.query,
                    "normalized_query": norm,
                    "page_url": row.page_url,
                    "clicks": row.clicks,
                    "impressions": row.impressions,
                    "average_position": row.average_position,
                    "opportunity_count": 0,
                }
            )
        for norm, count in opp_counts.items():
            for item in input_rows:
                if item["normalized_query"] == norm:
                    item["opportunity_count"] = count
                    break
            else:
                sample = next((o for o in opp_rows if o.normalized_query == norm), None)
                if sample:
                    input_rows.append(
                        {
                            "query": sample.query,
                            "normalized_query": norm,
                            "page_url": sample.page_url,
                            "clicks": sample.clicks,
                            "impressions": sample.impressions,
                            "average_position": sample.average_position,
                            "opportunity_count": count,
                        }
                    )

        records = build_query_records(input_rows)
        if len(records) > DEFAULT_TOPIC_THRESHOLDS.max_queries_per_analysis:
            records = sorted(records, key=lambda r: -r.impressions)[: DEFAULT_TOPIC_THRESHOLDS.max_queries_per_analysis]

        drafts = cluster_queries(records)

        await self.db.execute(delete(TopicCluster).where(TopicCluster.sync_id == sync.id))
        await self.db.flush()

        created = 0
        for draft in drafts:
            cluster = TopicCluster(
                sync_id=sync.id,
                organization_id=organization_id,
                site_url=sync.site_url,
                cluster_key=draft.cluster_key,
                topic_label=draft.topic_label[:512],
                representative_query=draft.representative_query,
                query_count=len(draft.queries),
                page_count=draft.page_count,
                total_clicks=draft.total_clicks,
                total_impressions=draft.total_impressions,
                aggregate_ctr=draft.aggregate_ctr,
                weighted_average_position=draft.weighted_average_position,
                opportunity_count=draft.opportunity_count,
                multi_page_signal=draft.multi_page_signal,
                is_singleton=draft.is_singleton,
                algorithm_version=ALGORITHM_VERSION,
                status="active",
            )
            self.db.add(cluster)
            await self.db.flush()

            rep_tokens = tokenize_query(draft.queries[0].normalized_query)
            for q in draft.queries:
                sim = jaccard_similarity(rep_tokens, q.tokens)
                reason = "singleton" if draft.is_singleton else "jaccard_token_overlap"
                self.db.add(
                    TopicClusterQuery(
                        topic_cluster_id=cluster.id,
                        organization_id=organization_id,
                        query=q.query,
                        normalized_query=q.normalized_query[:1024],
                        similarity_score=round(sim, 4),
                        membership_reason=reason,
                        clicks=q.clicks,
                        impressions=q.impressions,
                        ctr=q.ctr,
                        average_position=q.average_position,
                        opportunity_count=q.opportunity_count,
                        is_representative=q.normalized_query == draft.queries[0].normalized_query,
                    )
                )
            for i, page in enumerate(draft.pages):
                self.db.add(
                    TopicClusterPage(
                        topic_cluster_id=cluster.id,
                        organization_id=organization_id,
                        page_url=page["page_url"],
                        clicks=page["clicks"],
                        impressions=page["impressions"],
                        ctr=page["ctr"],
                        average_position=page["average_position"],
                        is_primary=i == 0,
                    )
                )
            created += 1

        await self.db.flush()
        return {
            "sync_id": sync.id,
            "topics_created": created,
            "clustered_queries": sum(len(d.queries) for d in drafts),
            "algorithm_version": ALGORITHM_VERSION,
        }

    async def list_topics(
        self,
        *,
        organization_id: UUID,
        sync_id: UUID | None = None,
        is_singleton: bool | None = None,
        multi_page_signal: bool | None = None,
        min_impressions: float | None = None,
        sort: str = "impressions",
        limit: int = 100,
        offset: int = 0,
    ) -> list[TopicCluster]:
        sync = await self._resolve_sync(organization_id, sync_id)
        q = select(TopicCluster).where(
            TopicCluster.sync_id == sync.id,
            TopicCluster.organization_id == organization_id,
            TopicCluster.algorithm_version == ALGORITHM_VERSION,
        )
        if is_singleton is not None:
            q = q.where(TopicCluster.is_singleton == is_singleton)
        if multi_page_signal is not None:
            q = q.where(TopicCluster.multi_page_signal == multi_page_signal)
        if min_impressions is not None:
            q = q.where(TopicCluster.total_impressions >= min_impressions)
        sort_col = ALLOWED_SORT.get(sort, TopicCluster.total_impressions)
        q = q.order_by(sort_col.desc()).offset(offset).limit(min(limit, 500))
        return list((await self.db.execute(q)).scalars().all())

    async def get_topic(self, *, organization_id: UUID, topic_id: UUID) -> TopicCluster:
        row = await self.db.scalar(
            select(TopicCluster).where(
                TopicCluster.id == topic_id,
                TopicCluster.organization_id == organization_id,
            ).limit(1)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic cluster not found")
        return row

    async def list_queries(self, *, organization_id: UUID, topic_id: UUID) -> list[TopicClusterQuery]:
        await self.get_topic(organization_id=organization_id, topic_id=topic_id)
        rows = (
            await self.db.execute(
                select(TopicClusterQuery)
                .where(
                    TopicClusterQuery.topic_cluster_id == topic_id,
                    TopicClusterQuery.organization_id == organization_id,
                )
                .order_by(TopicClusterQuery.impressions.desc())
            )
        ).scalars().all()
        return list(rows)

    async def list_pages(self, *, organization_id: UUID, topic_id: UUID) -> list[TopicClusterPage]:
        await self.get_topic(organization_id=organization_id, topic_id=topic_id)
        rows = (
            await self.db.execute(
                select(TopicClusterPage)
                .where(
                    TopicClusterPage.topic_cluster_id == topic_id,
                    TopicClusterPage.organization_id == organization_id,
                )
                .order_by(TopicClusterPage.impressions.desc())
            )
        ).scalars().all()
        return list(rows)

    async def summary(self, *, organization_id: UUID, sync_id: UUID | None = None) -> dict:
        sync = await self._resolve_sync(organization_id, sync_id)
        rows = (
            await self.db.execute(
                select(TopicCluster).where(
                    TopicCluster.sync_id == sync.id,
                    TopicCluster.organization_id == organization_id,
                    TopicCluster.algorithm_version == ALGORITHM_VERSION,
                )
            )
        ).scalars().all()
        total_topics = len(rows)
        total_queries = sum(r.query_count for r in rows)
        singletons = sum(1 for r in rows if r.is_singleton)
        avg_q = (total_queries / total_topics) if total_topics else 0.0
        multi_page = sum(1 for r in rows if r.multi_page_signal)

        by_imp = sorted(rows, key=lambda r: -r.total_impressions)[:5]
        by_clicks = sorted(rows, key=lambda r: -r.total_clicks)[:5]
        by_opp = sorted(rows, key=lambda r: -r.opportunity_count)[:5]

        return {
            "sync_id": sync.id,
            "site_url": sync.site_url,
            "algorithm_version": ALGORITHM_VERSION,
            "total_topics": total_topics,
            "total_clustered_queries": total_queries,
            "singleton_topics": singletons,
            "average_queries_per_topic": round(avg_q, 2),
            "multi_page_topic_count": multi_page,
            "top_by_impressions": by_imp,
            "top_by_clicks": by_clicks,
            "top_by_opportunity_count": by_opp,
            "disclaimer": DISCLAIMER,
        }

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
