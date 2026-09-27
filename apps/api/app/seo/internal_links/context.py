"""Evidence context for internal-link discovery (M9.12)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.keyword_opportunity import KeywordOpportunity
from app.models.seo import SeoCrawl, SeoCrawlPage
from app.models.seo_content_brief import SeoContentBrief
from app.models.seo_generated_content import SeoGeneratedContent
from app.models.seo_onpage_optimization import SeoOnPageFinding
from app.models.topic_cluster import TopicCluster, TopicClusterPage
from app.seo.internal_links.thresholds import InternalLinkLimits
from app.seo.internal_links.types import PageRecord
from app.seo.internal_links.url_validation import normalize_internal_url
from app.seo.topics.tokens import tokenize_query


@dataclass
class InternalLinkContext:
    site_root: str
    crawl_id: UUID | None
    pages: dict[str, PageRecord] = field(default_factory=dict)
    page_keywords: dict[str, set[str]] = field(default_factory=dict)
    page_topics: dict[str, set[str]] = field(default_factory=dict)
    onpage_finding_refs: list[dict[str, Any]] = field(default_factory=list)
    content: SeoGeneratedContent | None = None
    brief: SeoContentBrief | None = None
    limitations: list[str] = field(default_factory=list)

    @property
    def internal_urls(self) -> set[str]:
        return set(self.pages.keys())


async def build_internal_link_context(
    db: AsyncSession,
    *,
    organization_id: UUID,
    content: SeoGeneratedContent | None,
    brief: SeoContentBrief | None,
    limits: InternalLinkLimits,
) -> InternalLinkContext:
    site_root = _resolve_site_root(content, brief)
    ctx = InternalLinkContext(site_root=site_root, crawl_id=None, content=content, brief=brief)

    if not site_root:
        ctx.limitations.append("No site root URL available from content or brief; internal-link discovery skipped.")
        return ctx

    crawl = await db.scalar(
        select(SeoCrawl)
        .where(SeoCrawl.organization_id == organization_id, SeoCrawl.status == "completed")
        .order_by(SeoCrawl.completed_at.desc())
        .limit(1)
    )
    if not crawl:
        ctx.limitations.append("No completed SEO crawl available; opportunities limited to content/brief evidence.")
    else:
        ctx.crawl_id = crawl.id
        if not site_root:
            ctx.site_root = crawl.root_url
            site_root = crawl.root_url
        rows = list(
            (
                await db.execute(
                    select(SeoCrawlPage)
                    .where(
                        SeoCrawlPage.organization_id == organization_id,
                        SeoCrawlPage.crawl_id == crawl.id,
                    )
                    .limit(limits.max_source_pages + limits.max_target_candidates)
                )
            ).scalars().all()
        )
        inbound: dict[str, int] = {}
        outbound: dict[str, set[str]] = {}
        for row in rows:
            norm = normalize_internal_url(row.url, site_root=site_root)
            if not norm:
                norm = normalize_internal_url(row.final_url or row.url, site_root=site_root)
            if not norm:
                continue
            obs = row.observations or {}
            title = str(obs.get("title") or obs.get("meta_title") or "").strip()
            headings = [str(h.get("text", h)) for h in (obs.get("headings") or []) if h][:10]
            page = PageRecord(
                page_id=row.id,
                url=row.url,
                normalized_url=norm,
                title=title or norm,
                depth=int(row.depth or 0),
                headings=headings,
            )
            ctx.pages[norm] = page
            links: set[str] = set()
            for link in obs.get("internal_links") or []:
                nlink = normalize_internal_url(str(link), site_root=site_root)
                if nlink:
                    links.add(nlink)
                    inbound[nlink] = inbound.get(nlink, 0) + 1
            outbound[norm] = links

        for norm, page in ctx.pages.items():
            page.inbound_count = inbound.get(norm, 0)
            page.outbound_links = outbound.get(norm, set())

    await _load_keyword_topics(db, organization_id=organization_id, ctx=ctx, limits=limits)
    await _load_onpage_refs(db, organization_id=organization_id, content=content, ctx=ctx)
    return ctx


def _resolve_site_root(content: SeoGeneratedContent | None, brief: SeoContentBrief | None) -> str:
    for candidate in [
        getattr(content, "target_url", None) if content else None,
        getattr(brief, "target_url", None) if brief else None,
    ]:
        if candidate and str(candidate).startswith(("http://", "https://")):
            from urllib.parse import urlparse

            parsed = urlparse(str(candidate))
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}"
    return ""


async def _load_keyword_topics(
    db: AsyncSession,
    *,
    organization_id: UUID,
    ctx: InternalLinkContext,
    limits: InternalLinkLimits,
) -> None:
    kw_rows = list(
        (
            await db.execute(
                select(KeywordOpportunity)
                .where(KeywordOpportunity.organization_id == organization_id)
                .limit(limits.max_keyword_comparisons)
            )
        ).scalars().all()
    )
    for row in kw_rows:
        page_url = normalize_internal_url(row.page_url or "", site_root=ctx.site_root) if row.page_url else None
        if page_url:
            ctx.page_keywords.setdefault(page_url, set()).add(row.normalized_query or row.query)
        tokens = tokenize_query((row.normalized_query or row.query or "").casefold())
        if page_url and page_url in ctx.pages:
            ctx.pages[page_url].keywords.update(tokens)

    topic_rows = list(
        (
            await db.execute(
                select(TopicCluster)
                .where(TopicCluster.organization_id == organization_id)
                .limit(limits.max_topic_comparisons)
            )
        ).scalars().all()
    )
    for cluster in topic_rows:
        label = cluster.topic_label or cluster.representative_query or ""
        pages = list(
            (
                await db.execute(
                    select(TopicClusterPage)
                    .where(
                        TopicClusterPage.organization_id == organization_id,
                        TopicClusterPage.topic_cluster_id == cluster.id,
                    )
                    .limit(50)
                )
            ).scalars().all()
        )
        for tp in pages:
            norm = normalize_internal_url(tp.page_url, site_root=ctx.site_root)
            if norm:
                ctx.page_topics.setdefault(norm, set()).add(label)
                if norm in ctx.pages:
                    ctx.pages[norm].topics.add(label)


async def _load_onpage_refs(
    db: AsyncSession,
    *,
    organization_id: UUID,
    content: SeoGeneratedContent | None,
    ctx: InternalLinkContext,
) -> None:
    if not content:
        return
    rows = list(
        (
            await db.execute(
                select(SeoOnPageFinding)
                .where(
                    SeoOnPageFinding.organization_id == organization_id,
                    SeoOnPageFinding.generated_content_id == content.id,
                    SeoOnPageFinding.category == "internal_links",
                )
                .limit(20)
            )
        ).scalars().all()
    )
    for row in rows:
        ctx.onpage_finding_refs.append(
            {
                "source": "onpage_finding",
                "id": str(row.id),
                "reason": row.finding_type,
                "title": row.title,
            }
        )
