"""Deterministic content-gap signal generation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from app.seo.content_gap.matching import best_topic_match, classify_strength
from app.seo.content_gap.representation import PageRepresentation, extract_page_representation, user_topic_tokens
from app.seo.content_gap.thresholds import ALGORITHM_VERSION, DEFAULT_GAP_THRESHOLDS, ContentGapThresholds
from app.seo.topics.similarity import jaccard_similarity


@dataclass
class GapDraft:
    gap_type: str
    topic_label: str
    competitor_url: str | None
    user_topic_id: str | None
    user_query: str | None
    competitor_title: str | None
    competitor_h1: str | None
    similarity: float
    match_strength: str
    evidence: dict[str, Any]
    explanation: str
    dedupe_key: str
    competitor_id: str | None = None
    competitor_crawl_id: str | None = None
    competitor_page_id: str | None = None


@dataclass
class GapAnalysisInput:
    user_topics: list[dict]
    opportunities: list[dict]
    competitor_pages: list[dict]
    thresholds: ContentGapThresholds = field(default_factory=lambda: DEFAULT_GAP_THRESHOLDS)


def build_user_topics_for_matching(clusters: list[Any], queries_by_cluster: dict[str, list[str]]) -> list[dict]:
    topics: list[dict] = []
    for cluster in clusters:
        qtexts = queries_by_cluster.get(str(cluster.id), [])
        topics.append(
            {
                "id": cluster.id,
                "topic_label": cluster.topic_label,
                "representative_query": cluster.representative_query,
                "query_count": cluster.query_count,
                "page_count": cluster.page_count,
                "total_impressions": cluster.total_impressions,
                "tokens": user_topic_tokens(
                    topic_label=cluster.topic_label,
                    representative_query=cluster.representative_query,
                    queries=qtexts,
                ),
            }
        )
    return topics


def detect_content_gaps(inp: GapAnalysisInput) -> list[GapDraft]:
    if not inp.competitor_pages:
        return []
    if not inp.user_topics:
        return _gaps_without_user_topics(inp)

    drafts: list[GapDraft] = []
    comparisons = 0
    pages_by_user_topic: dict[str, list[PageRepresentation]] = {}

    for raw in inp.competitor_pages:
        if comparisons >= inp.thresholds.max_comparisons:
            break
        rep = extract_page_representation(url=str(raw["url"]), observations=raw.get("observations") or {})
        if not rep.tokens:
            continue
        comparisons += 1
        match = best_topic_match(rep.tokens, inp.user_topics, thresholds=inp.thresholds)

        if match is None or match.match_strength == "weak_match":
            if match is None or match.similarity < inp.thresholds.moderate_match:
                drafts.append(_draft(
                    gap_type="COMPETITOR_TOPIC_NO_USER_CLUSTER" if match is None else "COMPETITOR_LEXICAL_NO_USER_TOPIC",
                    rep=rep,
                    raw=raw,
                    match=match,
                    explanation=_explain_no_cluster(rep, match),
                ))
        elif match.match_strength == "moderate_match" and (
            match.page_count <= inp.thresholds.weak_user_page_count
            or match.total_impressions < inp.thresholds.weak_user_impressions
        ):
            drafts.append(_draft(
                gap_type="COMPETITOR_PAGE_WEAK_USER_COVERAGE",
                rep=rep,
                raw=raw,
                match=match,
                explanation=(
                    f"Competitor page overlaps user topic '{match.topic_label}' with similarity "
                    f"{match.similarity:.2f}, but user coverage appears limited "
                    f"({match.page_count} page(s), {match.total_impressions:.0f} impressions)."
                ),
            ))
        elif match.match_strength in {"strong_match", "moderate_match"}:
            pages_by_user_topic.setdefault(match.user_topic_id, []).append(rep)

    for topic_id, reps in pages_by_user_topic.items():
        if len(reps) < 2:
            continue
        sample = reps[0]
        match = best_topic_match(sample.tokens, inp.user_topics, thresholds=inp.thresholds)
        if not match:
            continue
        if match.page_count <= inp.thresholds.weak_user_page_count:
            drafts.append(_draft(
                gap_type="MULTI_COMPETITOR_LIMITED_USER",
                rep=sample,
                raw={"url": sample.url, "competitor_id": None, "crawl_id": None, "page_id": None},
                match=match,
                explanation=(
                    f"{len(reps)} competitor pages match user topic '{match.topic_label}'. "
                    f"User site has limited page coverage ({match.page_count} page(s))."
                ),
            ))
        if match.match_strength == "strong_match" and match.page_count <= 1:
            drafts.append(_draft(
                gap_type="USER_TOPIC_COMPETITOR_DEPTH",
                rep=sample,
                raw={"url": sample.url, "competitor_id": None, "crawl_id": None, "page_id": None},
                match=match,
                explanation=(
                    f"User topic '{match.topic_label}' strongly matches {len(reps)} competitor pages. "
                    f"This is a content depth signal, not a ranking claim."
                ),
            ))

    for opp in inp.opportunities:
        norm = opp.get("normalized_query") or ""
        topic = _find_topic_for_query(norm, inp.user_topics)
        if topic and int(topic.get("page_count") or 0) == 0:
            drafts.append(
                GapDraft(
                    gap_type="USER_OPPORTUNITY_NO_STRONG_PAGE",
                    topic_label=str(opp.get("query") or norm),
                    competitor_url=None,
                    user_topic_id=str(topic["id"]),
                    user_query=str(opp.get("query") or norm),
                    competitor_title=None,
                    competitor_h1=None,
                    similarity=0.0,
                    match_strength="no_match",
                    evidence={
                        "source_a": "search_console",
                        "source_b": "keyword_opportunity",
                        "opportunity_type": opp.get("opportunity_type"),
                        "impressions": opp.get("impressions"),
                        "clicks": opp.get("clicks"),
                    },
                    explanation=(
                        f"Keyword opportunity '{opp.get('query')}' has no clearly associated strong page "
                        f"in topic cluster '{topic.get('topic_label')}'."
                    ),
                    dedupe_key=_dedupe("USER_OPPORTUNITY_NO_STRONG_PAGE", None, None, str(topic["id"]), norm),
                )
            )

    return _dedupe_drafts(drafts)


def _gaps_without_user_topics(inp: GapAnalysisInput) -> list[GapDraft]:
    drafts: list[GapDraft] = []
    for raw in inp.competitor_pages[:50]:
        rep = extract_page_representation(url=str(raw["url"]), observations=raw.get("observations") or {})
        drafts.append(_draft(
            gap_type="COMPETITOR_TOPIC_NO_USER_CLUSTER",
            rep=rep,
            raw=raw,
            match=None,
            explanation="Competitor page observed, but no user topic clusters exist for comparison.",
        ))
    return _dedupe_drafts(drafts)


def _find_topic_for_query(normalized_query: str, topics: list[dict]) -> dict | None:
    from app.seo.topics.tokens import tokenize_query

    q_tokens = tokenize_query(normalized_query)
    best = None
    best_sim = 0.0
    for topic in topics:
        sim = jaccard_similarity(q_tokens, topic.get("tokens") or set())
        if sim > best_sim:
            best_sim = sim
            best = topic
    return best if best_sim >= DEFAULT_GAP_THRESHOLDS.weak_match else None


def _draft(
    *,
    gap_type: str,
    rep: PageRepresentation,
    raw: dict,
    match,
    explanation: str,
) -> GapDraft:
    return GapDraft(
        gap_type=gap_type,
        topic_label=rep.topic_label,
        competitor_url=rep.url,
        user_topic_id=match.user_topic_id if match else None,
        user_query=match.representative_query if match else None,
        competitor_title=rep.title,
        competitor_h1=rep.h1,
        similarity=match.similarity if match else 0.0,
        match_strength=match.match_strength if match else "no_match",
        evidence={
            "source_a": "search_console_topics",
            "source_b": "competitor_crawl",
            "competitor_tokens_sample": sorted(list(rep.tokens))[:20],
            "shared_terms": sorted(list(rep.tokens))[:10],
            "similarity_method": "jaccard_token_overlap",
            "competitor_crawl_timestamp": raw.get("fetched_at"),
            "data_honesty": "No competitor rankings, traffic, or search volume inferred.",
        },
        explanation=explanation,
        dedupe_key=_dedupe(gap_type, raw.get("competitor_id"), rep.url, match.user_topic_id if match else None, None),
        competitor_id=str(raw.get("competitor_id")) if raw.get("competitor_id") else None,
        competitor_crawl_id=str(raw.get("crawl_id")) if raw.get("crawl_id") else None,
        competitor_page_id=str(raw.get("page_id")) if raw.get("page_id") else None,
    )


def _explain_no_cluster(rep: PageRepresentation, match) -> str:
    if match is None:
        return (
            f"Competitor page '{rep.url}' contains terms including "
            f"{', '.join(sorted(rep.tokens)[:5])}. No user topic cluster meets similarity threshold."
        )
    return (
        f"Competitor page overlaps weakly with user topic '{match.topic_label}' "
        f"(similarity {match.similarity:.2f}) — below strong match threshold."
    )


def _dedupe(gap_type: str, competitor_id, url, topic_id, query) -> str:
    raw = f"{ALGORITHM_VERSION}|{gap_type}|{competitor_id}|{url}|{topic_id}|{query}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def _dedupe_drafts(drafts: list[GapDraft]) -> list[GapDraft]:
    seen: set[str] = set()
    out: list[GapDraft] = []
    for d in drafts:
        if d.dedupe_key in seen:
            continue
        seen.add(d.dedupe_key)
        out.append(d)
    return out
