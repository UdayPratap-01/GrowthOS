"""Deterministic bounded evidence selection for SEO AI recommendations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.enums import SearchConsoleSyncStatus, SeoCrawlStatus, SeoFindingSeverity
from app.models.keyword_opportunity import KeywordOpportunity
from app.models.search_console import SearchConsoleOpportunity, SearchConsoleSync
from app.models.seo import SeoCrawl, SeoFinding
from app.models.seo_competitor import ContentGap as SeoContentGap
from app.models.seo_competitor import SeoCompetitorPage
from app.models.topic_cluster import TopicCluster, TopicClusterQuery
from app.seo.recommendations.thresholds import DEFAULT_SEO_RECOMMENDATION_LIMITS, UNAVAILABLE_COMPETITOR_FIELDS, SeoRecommendationLimits

_SEVERITY_ORDER = {
    SeoFindingSeverity.high: 0,
    SeoFindingSeverity.medium: 1,
    SeoFindingSeverity.low: 2,
    SeoFindingSeverity.info: 3,
}


@dataclass
class EvidenceSnapshot:
    sync_id: UUID | None
    site_url: str | None
    findings: list[dict[str, Any]] = field(default_factory=list)
    keyword_opportunities: list[dict[str, Any]] = field(default_factory=list)
    topic_clusters: list[dict[str, Any]] = field(default_factory=list)
    content_gaps: list[dict[str, Any]] = field(default_factory=list)
    search_console_opportunities: list[dict[str, Any]] = field(default_factory=list)
    competitor_pages: list[dict[str, Any]] = field(default_factory=list)
    unavailable_fields: dict[str, str] = field(default_factory=lambda: dict(UNAVAILABLE_COMPETITOR_FIELDS))
    index: dict[str, set[str]] = field(default_factory=dict)
    urls: set[str] = field(default_factory=set)
    keywords: set[str] = field(default_factory=set)
    topic_ids: set[str] = field(default_factory=set)

    def total_records(self) -> int:
        return (
            len(self.findings)
            + len(self.keyword_opportunities)
            + len(self.topic_clusters)
            + len(self.content_gaps)
            + len(self.search_console_opportunities)
            + len(self.competitor_pages)
        )

    def evidence_hash(self) -> str:
        payload = {
            "findings": [r["id"] for r in self.findings],
            "keywords": [r["id"] for r in self.keyword_opportunities],
            "topics": [r["id"] for r in self.topic_clusters],
            "gaps": [r["id"] for r in self.content_gaps],
            "gsc_opps": [r["id"] for r in self.search_console_opportunities],
            "comp_pages": [r["id"] for r in self.competitor_pages],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:64]

    def as_prompt_dict(self) -> dict[str, Any]:
        return {
            "sync_id": str(self.sync_id) if self.sync_id else None,
            "site_url": self.site_url,
            "unavailable_fields": self.unavailable_fields,
            "findings": self.findings,
            "keyword_opportunities": self.keyword_opportunities,
            "topic_clusters": self.topic_clusters,
            "content_gaps": self.content_gaps,
            "search_console_opportunities": self.search_console_opportunities,
            "competitor_pages": self.competitor_pages,
        }


async def build_evidence_snapshot(
    db: AsyncSession,
    *,
    organization_id: UUID,
    sync_id: UUID | None = None,
    limits: SeoRecommendationLimits | None = None,
) -> EvidenceSnapshot:
    limits = limits or _limits_from_settings()
    sync = await _resolve_sync(db, organization_id, sync_id)
    snapshot = EvidenceSnapshot(sync_id=sync.id if sync else None, site_url=sync.site_url if sync else None)

    crawl = await db.scalar(
        select(SeoCrawl)
        .where(SeoCrawl.organization_id == organization_id, SeoCrawl.status == SeoCrawlStatus.completed)
        .order_by(SeoCrawl.completed_at.desc())
        .limit(1)
    )
    if crawl:
        findings = (
            await db.execute(
                select(SeoFinding).where(
                    SeoFinding.organization_id == organization_id,
                    SeoFinding.crawl_id == crawl.id,
                )
            )
        ).scalars().all()
        findings.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 99), f.created_at))
        for row in findings[: limits.max_findings]:
            item = {
                "id": str(row.id),
                "source": "seo_finding",
                "rule_id": row.rule_id,
                "category": row.category,
                "severity": str(row.severity.value if hasattr(row.severity, "value") else row.severity),
                "url": row.url,
                "title": row.title,
                "description": row.description[:500],
                "recommendation": row.recommendation,
                "evidence": row.evidence,
            }
            snapshot.findings.append(item)
            snapshot.index.setdefault("seo_finding", set()).add(str(row.id))
            if row.url:
                snapshot.urls.add(row.url)

    if sync:
        kw_rows = (
            await db.execute(
                select(KeywordOpportunity)
                .where(
                    KeywordOpportunity.organization_id == organization_id,
                    KeywordOpportunity.sync_id == sync.id,
                )
                .order_by(KeywordOpportunity.impressions.desc())
                .limit(limits.max_keywords)
            )
        ).scalars().all()
        for row in kw_rows:
            item = {
                "id": str(row.id),
                "source": "keyword_opportunity",
                "query": row.query,
                "normalized_query": row.normalized_query,
                "opportunity_type": row.opportunity_type,
                "page_url": row.page_url,
                "clicks": row.clicks,
                "impressions": row.impressions,
                "ctr": row.ctr,
                "average_position": row.average_position,
                "explanation": row.explanation[:500],
            }
            snapshot.keyword_opportunities.append(item)
            snapshot.index.setdefault("keyword_opportunity", set()).add(str(row.id))
            snapshot.keywords.add(row.query)
            if row.page_url:
                snapshot.urls.add(row.page_url)

        topics = (
            await db.execute(
                select(TopicCluster)
                .where(
                    TopicCluster.organization_id == organization_id,
                    TopicCluster.sync_id == sync.id,
                )
                .order_by(TopicCluster.total_impressions.desc())
                .limit(limits.max_topics)
            )
        ).scalars().all()
        topic_ids = [t.id for t in topics]
        queries = {}
        if topic_ids:
            qrows = (
                await db.execute(
                    select(TopicClusterQuery).where(
                        TopicClusterQuery.organization_id == organization_id,
                        TopicClusterQuery.topic_cluster_id.in_(topic_ids),
                    )
                )
            ).scalars().all()
            for q in qrows:
                queries.setdefault(str(q.topic_cluster_id), []).append(q.query)

        for row in topics:
            item = {
                "id": str(row.id),
                "source": "topic_cluster",
                "topic_label": row.topic_label,
                "representative_query": row.representative_query,
                "query_count": row.query_count,
                "page_count": row.page_count,
                "total_impressions": row.total_impressions,
                "total_clicks": row.total_clicks,
                "multi_page_signal": row.multi_page_signal,
                "opportunity_count": row.opportunity_count,
                "queries": queries.get(str(row.id), [])[:10],
            }
            snapshot.topic_clusters.append(item)
            snapshot.index.setdefault("topic_cluster", set()).add(str(row.id))
            snapshot.topic_ids.add(str(row.id))
            snapshot.keywords.update(queries.get(str(row.id), [])[:5])

        gaps = (
            await db.execute(
                select(SeoContentGap)
                .where(
                    SeoContentGap.organization_id == organization_id,
                    SeoContentGap.sync_id == sync.id,
                )
                .order_by(SeoContentGap.similarity.desc())
                .limit(limits.max_gaps)
            )
        ).scalars().all()
        for row in gaps:
            item = {
                "id": str(row.id),
                "source": "content_gap",
                "gap_type": row.gap_type,
                "topic_label": row.topic_label,
                "competitor_url": row.competitor_url,
                "user_topic_id": str(row.user_topic_id) if row.user_topic_id else None,
                "user_query": row.user_query,
                "similarity": row.similarity,
                "match_strength": row.match_strength,
                "explanation": row.explanation[:500],
                "evidence": row.evidence,
            }
            snapshot.content_gaps.append(item)
            snapshot.index.setdefault("content_gap", set()).add(str(row.id))
            if row.competitor_url:
                snapshot.urls.add(row.competitor_url)
            if row.user_query:
                snapshot.keywords.add(row.user_query)
            if row.user_topic_id:
                snapshot.topic_ids.add(str(row.user_topic_id))

        gsc_opps = (
            await db.execute(
                select(SearchConsoleOpportunity)
                .where(
                    SearchConsoleOpportunity.organization_id == organization_id,
                    SearchConsoleOpportunity.sync_id == sync.id,
                )
                .order_by(SearchConsoleOpportunity.created_at.desc())
                .limit(20)
            )
        ).scalars().all()
        for row in gsc_opps:
            item = {
                "id": str(row.id),
                "source": "search_console_opportunity",
                "opportunity_type": row.opportunity_type,
                "query": row.query,
                "page_url": row.page_url,
                "clicks": row.clicks,
                "impressions": row.impressions,
                "explanation": row.explanation[:500],
                "evidence": row.evidence,
            }
            if row.query:
                snapshot.keywords.add(row.query)
            if row.page_url:
                snapshot.urls.add(row.page_url)
            snapshot.search_console_opportunities.append(item)
            snapshot.index.setdefault("search_console_opportunity", set()).add(str(row.id))

    comp_pages = (
        await db.execute(
            select(SeoCompetitorPage)
            .where(SeoCompetitorPage.organization_id == organization_id)
            .order_by(SeoCompetitorPage.created_at.desc())
            .limit(limits.max_competitor_pages)
        )
    ).scalars().all()
    for row in comp_pages:
        item = {
            "id": str(row.id),
            "source": "competitor_page",
            "url": row.url,
            "title": row.title,
            "h1": row.h1,
            "topic_label": row.topic_label,
            "observations_note": "Crawl observation only — not ranking or traffic data.",
        }
        snapshot.competitor_pages.append(item)
        snapshot.index.setdefault("competitor_page", set()).add(str(row.id))
        snapshot.urls.add(row.url)

    return snapshot


def _limits_from_settings() -> SeoRecommendationLimits:
    settings = get_settings()
    return SeoRecommendationLimits(
        max_findings=getattr(settings, "seo_recommendation_max_findings", 20),
        max_keywords=getattr(settings, "seo_recommendation_max_keywords", 30),
        max_topics=getattr(settings, "seo_recommendation_max_topics", 20),
        max_gaps=getattr(settings, "seo_recommendation_max_gaps", 20),
        max_competitor_pages=getattr(settings, "seo_recommendation_max_competitor_pages", 15),
        max_recommendations_per_run=getattr(settings, "seo_recommendation_max_per_run", 15),
        max_prompt_chars=getattr(settings, "seo_recommendation_max_prompt_chars", 120_000),
    )


async def _resolve_sync(db: AsyncSession, organization_id: UUID, sync_id: UUID | None) -> SearchConsoleSync | None:
    if sync_id:
        return await db.scalar(
            select(SearchConsoleSync).where(
                SearchConsoleSync.id == sync_id,
                SearchConsoleSync.organization_id == organization_id,
            ).limit(1)
        )
    return await db.scalar(
        select(SearchConsoleSync)
        .where(
            SearchConsoleSync.organization_id == organization_id,
            SearchConsoleSync.status == SearchConsoleSyncStatus.completed,
        )
        .order_by(SearchConsoleSync.completed_at.desc())
        .limit(1)
    )
