"""Keyword opportunity engine — analysis over persisted M9.3 Search Console data."""

from __future__ import annotations

from collections import Counter
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import KeywordOpportunityPriority, SearchConsoleSyncStatus
from app.models.keyword_opportunity import KeywordOpportunity
from app.models.search_console import SearchConsolePerformanceRow, SearchConsoleSync
from app.seo.keywords.aggregate import aggregate_performance_rows
from app.seo.keywords.normalize import normalize_query
from app.seo.keywords.rules import detect_keyword_opportunities

DISCLAIMER = (
    "Keyword opportunities are derived from Search Console performance data. "
    "Impressions are not keyword search volume. Average position is not an exact rank. "
    "Opportunity signals are observational — not traffic forecasts."
)

ALLOWED_SORT = {
    "impressions": KeywordOpportunity.impressions,
    "clicks": KeywordOpportunity.clicks,
    "ctr": KeywordOpportunity.ctr,
    "average_position": KeywordOpportunity.average_position,
    "priority_score": KeywordOpportunity.priority_score,
    "created_at": KeywordOpportunity.created_at,
}


class KeywordOpportunityService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def analyze(
        self,
        *,
        organization_id: UUID,
        sync_id: UUID | None = None,
        brand_terms: list[str] | None = None,
    ) -> dict:
        sync = await self._resolve_sync(organization_id, sync_id)
        if sync.status != SearchConsoleSyncStatus.completed:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Search Console sync not completed")

        rows = (
            await self.db.execute(
                select(SearchConsolePerformanceRow).where(
                    SearchConsolePerformanceRow.sync_id == sync.id,
                    SearchConsolePerformanceRow.organization_id == organization_id,
                    SearchConsolePerformanceRow.dimension_type.in_(["query", "query_page"]),
                )
            )
        ).scalars().all()

        queries, query_pages = aggregate_performance_rows(list(rows))
        drafts = detect_keyword_opportunities(queries, query_pages, brand_terms=brand_terms or [])

        await self.db.execute(delete(KeywordOpportunity).where(KeywordOpportunity.sync_id == sync.id))
        for draft in drafts:
            self.db.add(
                KeywordOpportunity(
                    sync_id=sync.id,
                    organization_id=organization_id,
                    site_url=sync.site_url,
                    query=draft.query,
                    normalized_query=draft.normalized_query[:1024],
                    page_url=draft.page_url,
                    opportunity_type=draft.opportunity_type,
                    rule_id=draft.rule_id,
                    status="open",
                    priority=KeywordOpportunityPriority(draft.priority),
                    priority_score=draft.priority_score,
                    clicks=draft.clicks,
                    impressions=draft.impressions,
                    ctr=draft.ctr,
                    average_position=draft.average_position,
                    previous_clicks=draft.previous_clicks,
                    previous_impressions=draft.previous_impressions,
                    previous_ctr=draft.previous_ctr,
                    previous_average_position=draft.previous_average_position,
                    change_metrics=draft.change_metrics,
                    query_classification=draft.query_classification,
                    page_associations=draft.page_associations,
                    evidence={**draft.evidence, "data_provenance": "keyword_opportunity_engine"},
                    explanation=draft.explanation,
                    date_start=sync.effective_start_date,
                    date_end=sync.effective_end_date,
                    dedupe_key=draft.dedupe_key[:512],
                )
            )
        await self.db.flush()
        return {
            "sync_id": sync.id,
            "unique_queries": len(queries),
            "opportunities_created": len(drafts),
        }

    async def list_opportunities(
        self,
        *,
        organization_id: UUID,
        sync_id: UUID | None = None,
        opportunity_type: str | None = None,
        priority: str | None = None,
        status_filter: str | None = None,
        query: str | None = None,
        page_url: str | None = None,
        min_impressions: float | None = None,
        sort: str = "impressions",
        limit: int = 100,
        offset: int = 0,
    ) -> list[KeywordOpportunity]:
        sync = await self._resolve_sync(organization_id, sync_id)
        q = select(KeywordOpportunity).where(
            KeywordOpportunity.sync_id == sync.id,
            KeywordOpportunity.organization_id == organization_id,
        )
        if opportunity_type:
            q = q.where(KeywordOpportunity.opportunity_type == opportunity_type)
        if priority:
            q = q.where(KeywordOpportunity.priority == priority.lower())
        if status_filter:
            q = q.where(KeywordOpportunity.status == status_filter)
        if query:
            norm = normalize_query(query)
            if norm:
                q = q.where(KeywordOpportunity.normalized_query == norm)
        if page_url:
            q = q.where(KeywordOpportunity.page_url == page_url)
        if min_impressions is not None:
            q = q.where(KeywordOpportunity.impressions >= min_impressions)

        sort_col = ALLOWED_SORT.get(sort, KeywordOpportunity.impressions)
        q = q.order_by(sort_col.desc()).offset(offset).limit(min(limit, 500))
        return list((await self.db.execute(q)).scalars().all())

    async def get_opportunity(self, *, organization_id: UUID, opportunity_id: UUID) -> KeywordOpportunity:
        row = await self.db.scalar(
            select(KeywordOpportunity).where(
                KeywordOpportunity.id == opportunity_id,
                KeywordOpportunity.organization_id == organization_id,
            ).limit(1)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Keyword opportunity not found")
        return row

    async def get_by_query(self, *, organization_id: UUID, query: str, sync_id: UUID | None = None) -> list[KeywordOpportunity]:
        norm = normalize_query(query)
        if not norm:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid query")
        sync = await self._resolve_sync(organization_id, sync_id)
        rows = (
            await self.db.execute(
                select(KeywordOpportunity).where(
                    KeywordOpportunity.sync_id == sync.id,
                    KeywordOpportunity.organization_id == organization_id,
                    KeywordOpportunity.normalized_query == norm,
                )
            )
        ).scalars().all()
        return list(rows)

    async def get_by_page(self, *, organization_id: UUID, page_url: str, sync_id: UUID | None = None) -> list[KeywordOpportunity]:
        sync = await self._resolve_sync(organization_id, sync_id)
        rows = (
            await self.db.execute(
                select(KeywordOpportunity).where(
                    KeywordOpportunity.sync_id == sync.id,
                    KeywordOpportunity.organization_id == organization_id,
                    KeywordOpportunity.page_url == page_url,
                )
            )
        ).scalars().all()
        return list(rows)

    async def summary(self, *, organization_id: UUID, sync_id: UUID | None = None) -> dict:
        sync = await self._resolve_sync(organization_id, sync_id)
        rows = (
            await self.db.execute(
                select(KeywordOpportunity).where(
                    KeywordOpportunity.sync_id == sync.id,
                    KeywordOpportunity.organization_id == organization_id,
                )
            )
        ).scalars().all()
        perf = (
            await self.db.execute(
                select(SearchConsolePerformanceRow.query).where(
                    SearchConsolePerformanceRow.sync_id == sync.id,
                    SearchConsolePerformanceRow.organization_id == organization_id,
                    SearchConsolePerformanceRow.dimension_type.in_(["query", "query_page"]),
                )
            )
        ).scalars().all()
        unique_queries = len({normalize_query(q) or q for q in perf if q})
        by_type = dict(Counter(r.opportunity_type for r in rows))
        by_priority = dict(Counter(str(r.priority.value if hasattr(r.priority, "value") else r.priority) for r in rows))
        return {
            "sync_id": sync.id,
            "site_url": sync.site_url,
            "unique_queries": unique_queries,
            "total_opportunities": len(rows),
            "by_type": by_type,
            "by_priority": by_priority,
            "near_page_one_count": sum(1 for r in rows if r.rule_id == "KW_OPP_NEAR_PAGE_ONE"),
            "high_impression_low_ctr_count": sum(1 for r in rows if r.rule_id == "KW_OPP_HIGH_IMPRESSION_LOW_CTR"),
            "multi_page_query_count": sum(1 for r in rows if r.rule_id == "KW_OPP_MULTI_PAGE_RANKING"),
            "improving_count": sum(1 for r in rows if r.rule_id in {"KW_OPP_GROWING_CLICKS", "KW_OPP_GROWING_IMPRESSIONS"}),
            "declining_count": sum(1 for r in rows if r.rule_id in {"KW_OPP_DECLINING_CLICKS", "KW_OPP_DECLINING_IMPRESSIONS"}),
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
