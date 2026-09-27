"""Deterministic brief context builder from M9.7 recommendation + evidence (M9.8)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.enums import SeoCrawlStatus
from app.models.keyword_opportunity import KeywordOpportunity
from app.models.search_console import SearchConsoleOpportunity, SearchConsoleSync
from app.models.seo import SeoCrawl, SeoCrawlPage, SeoFinding
from app.models.seo_competitor import ContentGap as SeoContentGap
from app.models.seo_competitor import SeoCompetitorPage
from app.models.seo_recommendation import SeoRecommendation
from app.models.topic_cluster import TopicCluster, TopicClusterQuery
from app.seo.content_briefs.thresholds import (
    DEFAULT_SEO_CONTENT_BRIEF_LIMITS,
    UNAVAILABLE_COMPETITOR_FIELDS,
    SeoContentBriefLimits,
)


@dataclass
class BriefContext:
    recommendation_id: UUID
    recommendation_type: str
    recommendation_title: str
    recommendation_summary: str
    recommendation_rationale: str
    recommended_action: str
    expected_outcome: str
    site_url: str | None = None
    sync_id: UUID | None = None
    resolved_evidence: list[dict[str, Any]] = field(default_factory=list)
    recommendation_evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    affected_urls: list[str] = field(default_factory=list)
    affected_keywords: list[str] = field(default_factory=list)
    affected_topics: list[str] = field(default_factory=list)
    competitor_context: dict[str, Any] = field(default_factory=dict)
    internal_link_candidates: list[str] = field(default_factory=list)
    unavailable_fields: dict[str, str] = field(default_factory=lambda: dict(UNAVAILABLE_COMPETITOR_FIELDS))
    index: dict[str, set[str]] = field(default_factory=dict)
    keywords: set[str] = field(default_factory=set)
    primary_keyword_candidates: set[str] = field(default_factory=set)
    urls: set[str] = field(default_factory=set)
    internal_urls: set[str] = field(default_factory=set)
    topic_ids: set[str] = field(default_factory=set)
    topic_labels: dict[str, str] = field(default_factory=dict)

    def context_hash(self) -> str:
        payload = {
            "recommendation_id": str(self.recommendation_id),
            "evidence": sorted(f"{e['source']}:{e['id']}" for e in self.resolved_evidence),
            "internal_links": sorted(self.internal_link_candidates),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:64]

    def as_prompt_dict(self) -> dict[str, Any]:
        return {
            "recommendation": {
                "id": str(self.recommendation_id),
                "type": self.recommendation_type,
                "title": self.recommendation_title,
                "summary": self.recommendation_summary,
                "rationale": self.recommendation_rationale,
                "recommended_action": self.recommended_action,
                "expected_outcome": self.expected_outcome,
                "affected_urls": self.affected_urls,
                "affected_keywords": self.affected_keywords,
                "affected_topics": self.affected_topics,
                "competitor_context": self.competitor_context,
                "evidence_refs": self.recommendation_evidence_refs,
            },
            "sync_id": str(self.sync_id) if self.sync_id else None,
            "site_url": self.site_url,
            "unavailable_fields": self.unavailable_fields,
            "resolved_evidence": self.resolved_evidence,
            "internal_link_candidates": self.internal_link_candidates,
            "primary_keyword_candidates": sorted(self.primary_keyword_candidates),
        }


async def build_brief_context(
    db: AsyncSession,
    *,
    organization_id: UUID,
    recommendation: SeoRecommendation,
    limits: SeoContentBriefLimits | None = None,
) -> BriefContext:
    limits = limits or _limits_from_settings()
    ctx = BriefContext(
        recommendation_id=recommendation.id,
        recommendation_type=recommendation.recommendation_type,
        recommendation_title=recommendation.title,
        recommendation_summary=recommendation.summary,
        recommendation_rationale=recommendation.rationale,
        recommended_action=recommendation.recommended_action,
        expected_outcome=recommendation.expected_outcome,
        sync_id=recommendation.sync_id,
        recommendation_evidence_refs=list(recommendation.evidence_refs or []),
        affected_urls=list(recommendation.affected_urls or []),
        affected_keywords=list(recommendation.affected_keywords or []),
        affected_topics=list(recommendation.affected_topics or []),
        competitor_context=dict(recommendation.competitor_context or {}),
    )

    for ref in (recommendation.evidence_refs or [])[: limits.max_evidence_records]:
        record = await _resolve_evidence(db, organization_id=organization_id, source=ref.get("source"), evidence_id=ref.get("id"))
        if record:
            ctx.resolved_evidence.append(record)
            src = record["source"]
            ctx.index.setdefault(src, set()).add(record["id"])
            _enrich_context_from_record(ctx, record)

    for kw in ctx.affected_keywords:
        ctx.keywords.add(kw)
        ctx.primary_keyword_candidates.add(kw)
    for url in ctx.affected_urls:
        ctx.urls.add(url)

    crawl = await db.scalar(
        select(SeoCrawl)
        .where(SeoCrawl.organization_id == organization_id, SeoCrawl.status == SeoCrawlStatus.completed)
        .order_by(SeoCrawl.completed_at.desc())
        .limit(1)
    )
    if crawl:
        pages = (
            await db.execute(
                select(SeoCrawlPage.url)
                .where(
                    SeoCrawlPage.organization_id == organization_id,
                    SeoCrawlPage.crawl_id == crawl.id,
                )
                .limit(limits.max_internal_links)
            )
        ).scalars().all()
        ctx.internal_link_candidates = list(pages)
        ctx.internal_urls = set(pages)
        ctx.urls.update(pages)

    if recommendation.sync_id:
        sync_row = await db.scalar(
            select(SearchConsoleSync).where(
                SearchConsoleSync.id == recommendation.sync_id,
                SearchConsoleSync.organization_id == organization_id,
            ).limit(1)
        )
        if sync_row:
            ctx.site_url = sync_row.site_url

    return ctx


async def _resolve_evidence(
    db: AsyncSession,
    *,
    organization_id: UUID,
    source: str | None,
    evidence_id: str | None,
) -> dict[str, Any] | None:
    if not source or not evidence_id:
        return None
    try:
        eid = UUID(evidence_id)
    except ValueError:
        return None

    if source == "seo_finding":
        row = await db.scalar(
            select(SeoFinding).where(SeoFinding.id == eid, SeoFinding.organization_id == organization_id).limit(1)
        )
        if not row:
            return None
        return {
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

    if source == "keyword_opportunity":
        row = await db.scalar(
            select(KeywordOpportunity)
            .where(KeywordOpportunity.id == eid, KeywordOpportunity.organization_id == organization_id)
            .limit(1)
        )
        if not row:
            return None
        return {
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

    if source == "topic_cluster":
        row = await db.scalar(
            select(TopicCluster).where(TopicCluster.id == eid, TopicCluster.organization_id == organization_id).limit(1)
        )
        if not row:
            return None
        queries = (
            await db.execute(
                select(TopicClusterQuery.query)
                .where(TopicClusterQuery.topic_cluster_id == row.id, TopicClusterQuery.organization_id == organization_id)
                .limit(10)
            )
        ).scalars().all()
        return {
            "id": str(row.id),
            "source": "topic_cluster",
            "topic_label": row.topic_label,
            "representative_query": row.representative_query,
            "query_count": row.query_count,
            "page_count": row.page_count,
            "total_impressions": row.total_impressions,
            "queries": list(queries),
        }

    if source == "content_gap":
        row = await db.scalar(
            select(SeoContentGap).where(SeoContentGap.id == eid, SeoContentGap.organization_id == organization_id).limit(1)
        )
        if not row:
            return None
        return {
            "id": str(row.id),
            "source": "content_gap",
            "gap_type": row.gap_type,
            "topic_label": row.topic_label,
            "competitor_url": row.competitor_url,
            "user_query": row.user_query,
            "similarity": row.similarity,
            "match_strength": row.match_strength,
            "explanation": row.explanation[:500],
            "evidence": row.evidence,
        }

    if source == "search_console_opportunity":
        row = await db.scalar(
            select(SearchConsoleOpportunity)
            .where(SearchConsoleOpportunity.id == eid, SearchConsoleOpportunity.organization_id == organization_id)
            .limit(1)
        )
        if not row:
            return None
        return {
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

    if source == "competitor_page":
        row = await db.scalar(
            select(SeoCompetitorPage)
            .where(SeoCompetitorPage.id == eid, SeoCompetitorPage.organization_id == organization_id)
            .limit(1)
        )
        if not row:
            return None
        return {
            "id": str(row.id),
            "source": "competitor_page",
            "url": row.url,
            "title": row.title,
            "h1": row.h1,
            "topic_label": row.topic_label,
            "observations_note": "Crawl observation only — not ranking or traffic data.",
        }

    return None


def _enrich_context_from_record(ctx: BriefContext, record: dict[str, Any]) -> None:
    source = record.get("source")
    if source == "keyword_opportunity":
        if record.get("query"):
            ctx.keywords.add(record["query"])
            ctx.primary_keyword_candidates.add(record["query"])
        if record.get("page_url"):
            ctx.urls.add(record["page_url"])
    elif source == "topic_cluster":
        tid = record.get("id")
        if tid:
            ctx.topic_ids.add(tid)
            ctx.topic_labels[tid] = record.get("topic_label", "")
        if record.get("representative_query"):
            ctx.keywords.add(record["representative_query"])
            ctx.primary_keyword_candidates.add(record["representative_query"])
        for q in record.get("queries") or []:
            ctx.keywords.add(q)
    elif source == "content_gap":
        if record.get("user_query"):
            ctx.keywords.add(record["user_query"])
            ctx.primary_keyword_candidates.add(record["user_query"])
        if record.get("competitor_url"):
            ctx.urls.add(record["competitor_url"])
        if record.get("topic_label"):
            ctx.keywords.add(record["topic_label"])
    elif source == "search_console_opportunity":
        if record.get("query"):
            ctx.keywords.add(record["query"])
            ctx.primary_keyword_candidates.add(record["query"])
        if record.get("page_url"):
            ctx.urls.add(record["page_url"])
    elif source == "seo_finding":
        if record.get("url"):
            ctx.urls.add(record["url"])
    elif source == "competitor_page":
        if record.get("url"):
            ctx.urls.add(record["url"])


def _limits_from_settings() -> SeoContentBriefLimits:
    settings = get_settings()
    return SeoContentBriefLimits(
        max_evidence_records=getattr(settings, "seo_content_brief_max_evidence", 15),
        max_internal_links=getattr(settings, "seo_content_brief_max_internal_links", 20),
        max_prompt_chars=getattr(settings, "seo_content_brief_max_prompt_chars", 100_000),
    )
