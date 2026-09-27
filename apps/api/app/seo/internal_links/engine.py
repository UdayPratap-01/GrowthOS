"""Deterministic internal-link discovery engine (M9.12)."""

from __future__ import annotations

import re
from typing import Any

from app.models.seo_content_brief import SeoContentBrief
from app.models.seo_generated_content import SeoGeneratedContent
from app.seo.internal_links.anchor import generate_anchor_text
from app.seo.internal_links.context import InternalLinkContext
from app.seo.internal_links.scoring import score_link_candidate
from app.seo.internal_links.thresholds import DEFAULT_INTERNAL_LINK_LIMITS, InternalLinkLimits
from app.seo.internal_links.types import InternalLinkOpportunityDraft, PageRecord
from app.seo.internal_links.url_validation import is_internal_url, normalize_internal_url
from app.seo.topics.tokens import tokenize_query

NO_PUBLISHING = "M9.12 generates recommendations only. It does not publish or inject links into live websites."


def discover_internal_links(
    ctx: InternalLinkContext,
    *,
    limits: InternalLinkLimits | None = None,
) -> list[InternalLinkOpportunityDraft]:
    limits = limits or DEFAULT_INTERNAL_LINK_LIMITS
    if not ctx.site_root or not ctx.pages:
        return []

    source_urls = _resolve_source_urls(ctx)
    if not source_urls:
        return []

    existing_pairs = _existing_link_pairs(ctx)
    content_body_links = _links_in_content_body(ctx)

    drafts: list[InternalLinkOpportunityDraft] = []
    seen_pairs: set[tuple[str, str]] = set()

    for source_url in source_urls[: limits.max_source_pages]:
        source = ctx.pages.get(source_url)
        if not source:
            continue
        source_kw = _source_keywords(ctx, source_url)
        source_topic = _source_topic(ctx, source_url)

        candidates = _target_candidates(ctx, source_url, limits)
        for target_url in candidates:
            pair = (source_url, target_url)
            if pair in seen_pairs or source_url == target_url:
                continue
            target = ctx.pages.get(target_url)
            if not target:
                continue

            already = pair in existing_pairs or target_url in content_body_links.get(source_url, set())
            reciprocal = (target_url, source_url) in existing_pairs

            target_kw = list(ctx.page_keywords.get(target_url, set()))[:10]
            target_topic = next(iter(ctx.page_topics.get(target_url, set())), None)

            score, breakdown, confidence = score_link_candidate(
                source=source,
                target=target,
                source_keywords=source_kw,
                target_keywords=set(target_kw),
                source_topic=source_topic,
                target_topic=target_topic,
                already_linked=already,
                reciprocal=reciprocal,
            )
            min_score = limits.min_relevance_score
            if target.inbound_count == 0 and not already:
                min_score = max(0.25, min_score - 0.1)
            if score < min_score:
                continue

            opp_type = _classify_opportunity(source, target, source_topic, target_topic, already, reciprocal)
            anchor, alts, anchor_limits = generate_anchor_text(
                target=target,
                target_keywords=target_kw,
                target_topic=target_topic,
            )
            if not anchor:
                continue

            reason = _relationship_reason(opp_type, source, target, breakdown)
            evidence = _evidence_refs(ctx, source, target)
            opp_limits = [NO_PUBLISHING] + anchor_limits

            drafts.append(
                InternalLinkOpportunityDraft(
                    source_url=source_url,
                    target_url=target_url,
                    source_crawl_page_id=source.page_id,
                    target_crawl_page_id=target.page_id,
                    anchor_text=anchor,
                    anchor_alternatives=alts,
                    opportunity_type=opp_type,
                    relationship_reason=reason,
                    source_topic=source_topic,
                    target_topic=target_topic,
                    source_keywords=sorted(source_kw)[:10],
                    target_keywords=target_kw,
                    relevance_score=score,
                    confidence=confidence,
                    score_breakdown=breakdown,
                    evidence_refs=evidence,
                    limitations=opp_limits,
                )
            )
            seen_pairs.add(pair)
            if len(drafts) >= limits.max_opportunities:
                break
        if len(drafts) >= limits.max_opportunities:
            break

    drafts.sort(key=lambda d: (-d.relevance_score, d.target_url))
    return drafts[: limits.max_opportunities]


def _resolve_source_urls(ctx: InternalLinkContext) -> list[str]:
    urls: list[str] = []
    if ctx.content and ctx.content.target_url:
        norm = normalize_internal_url(ctx.content.target_url, site_root=ctx.site_root)
        if norm and norm in ctx.pages:
            urls.append(norm)
    for link in ctx.content.internal_link_targets or [] if ctx.content else []:
        if isinstance(link, dict):
            u = link.get("url")
        else:
            u = str(link)
        norm = normalize_internal_url(str(u), site_root=ctx.site_root)
        if norm and norm in ctx.pages and norm not in urls:
            urls.append(norm)
    if not urls and ctx.pages:
        urls = sorted(ctx.pages.keys())[: DEFAULT_INTERNAL_LINK_LIMITS.max_source_pages]
    return urls


def _target_candidates(ctx: InternalLinkContext, source_url: str, limits: InternalLinkLimits) -> list[str]:
    source = ctx.pages[source_url]
    candidates: set[str] = set()

    for topic in ctx.page_topics.get(source_url, set()):
        for url, topics in ctx.page_topics.items():
            if url != source_url and topic in topics:
                candidates.add(url)

    source_kw = ctx.page_keywords.get(source_url, set())
    for url, kws in ctx.page_keywords.items():
        if url == source_url:
            continue
        if source_kw & kws:
            candidates.add(url)

    for url, page in ctx.pages.items():
        if url == source_url:
            continue
        if page.inbound_count == 0:
            candidates.add(url)
        if page.depth >= limits.deep_page_min_depth:
            candidates.add(url)

    for out in source.outbound_links:
        if out in ctx.pages and out != source_url:
            candidates.discard(out)

    ranked = sorted(candidates, key=lambda u: (-ctx.pages[u].inbound_count, ctx.pages[u].depth, u))
    return ranked[: limits.max_target_candidates]


def _existing_link_pairs(ctx: InternalLinkContext) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for src, page in ctx.pages.items():
        for tgt in page.outbound_links:
            pairs.add((src, tgt))
    return pairs


def _links_in_content_body(ctx: InternalLinkContext) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    if not ctx.content:
        return result
    source = normalize_internal_url(ctx.content.target_url or "", site_root=ctx.site_root)
    if not source:
        return result
    found: set[str] = set()
    body = ctx.content.content or ""
    for match in re.findall(r'href=["\']([^"\']+)["\']', body, flags=re.I):
        norm = normalize_internal_url(match, site_root=ctx.site_root)
        if norm:
            found.add(norm)
    for item in ctx.content.internal_link_targets or []:
        u = item.get("url") if isinstance(item, dict) else str(item)
        norm = normalize_internal_url(str(u), site_root=ctx.site_root)
        if norm:
            found.add(norm)
    result[source] = found
    return result


def _source_keywords(ctx: InternalLinkContext, source_url: str) -> set[str]:
    kws = set(ctx.page_keywords.get(source_url, set()))
    if ctx.content:
        if ctx.content.primary_keyword:
            kws.update(tokenize_query(ctx.content.primary_keyword.casefold()))
        for sk in ctx.content.secondary_keywords or []:
            kws.update(tokenize_query(str(sk).casefold()))
    if ctx.brief and ctx.brief.primary_keyword:
        kws.update(tokenize_query(ctx.brief.primary_keyword.casefold()))
    return kws


def _source_topic(ctx: InternalLinkContext, source_url: str) -> str | None:
    topics = ctx.page_topics.get(source_url, set())
    if topics:
        return sorted(topics)[0]
    if ctx.content and ctx.content.target_topic:
        return ctx.content.target_topic
    if ctx.brief and ctx.brief.target_topic:
        return ctx.brief.target_topic
    return None


def _classify_opportunity(
    source: PageRecord,
    target: PageRecord,
    source_topic: str | None,
    target_topic: str | None,
    already: bool,
    reciprocal: bool,
) -> str:
    if target.inbound_count == 0:
        return "orphan_page"
    if target.depth >= DEFAULT_INTERNAL_LINK_LIMITS.deep_page_min_depth:
        return "deep_page"
    if source_topic and target_topic and source_topic == target_topic:
        return "topic_support"
    if source_topic and target_topic and source_topic != target_topic:
        return "content_cluster_navigation"
    if source.depth <= 1 and target.depth >= 2:
        return "hub_to_detail"
    if source.depth >= 2 and target.depth <= 1:
        return "detail_to_hub"
    if reciprocal:
        return "complementary_content"
    if already:
        return "contextual_support"
    return "related_content"


def _relationship_reason(opp_type: str, source: PageRecord, target: PageRecord, breakdown: dict[str, Any]) -> str:
    parts = [
        f"Recommend linking from '{source.title or source.normalized_url}' to '{target.title or target.normalized_url}'.",
        f"Type: {opp_type.replace('_', ' ')}.",
    ]
    if breakdown.get("keyword_overlap", 0) > 0:
        parts.append(f"Keyword overlap score: {breakdown['keyword_overlap']}.")
    if breakdown.get("topic_overlap", 0) > 0:
        parts.append(f"Topic overlap score: {breakdown['topic_overlap']}.")
    if target.inbound_count == 0:
        parts.append("Target page has no observed inbound internal links.")
    if target.depth >= DEFAULT_INTERNAL_LINK_LIMITS.deep_page_min_depth:
        parts.append(f"Target crawl depth is {target.depth}.")
    return " ".join(parts)


def _evidence_refs(ctx: InternalLinkContext, source: PageRecord, target: PageRecord) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if ctx.crawl_id:
        refs.append({"source": "seo_crawl", "id": str(ctx.crawl_id), "reason": "crawl graph"})
    if source.page_id:
        refs.append({"source": "seo_crawl_page", "id": str(source.page_id), "reason": "source page observation"})
    if target.page_id:
        refs.append({"source": "seo_crawl_page", "id": str(target.page_id), "reason": "target page observation"})
    if ctx.content:
        refs.append({"source": "generated_content", "id": str(ctx.content.id), "reason": "source content artifact"})
    if ctx.brief:
        refs.append({"source": "content_brief", "id": str(ctx.brief.id), "reason": "brief context"})
    refs.extend(ctx.onpage_finding_refs[:5])
    return refs
