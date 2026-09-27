"""Deterministic relevance scoring for internal-link opportunities (M9.12)."""

from __future__ import annotations

from app.seo.internal_links.types import PageRecord
from app.seo.topics.similarity import jaccard_similarity
from app.seo.topics.tokens import tokenize_query


def score_link_candidate(
    *,
    source: PageRecord,
    target: PageRecord,
    source_keywords: set[str],
    target_keywords: set[str],
    source_topic: str | None,
    target_topic: str | None,
    already_linked: bool,
    reciprocal: bool,
) -> tuple[float, dict[str, float], str]:
    """Return (relevance_score, breakdown, confidence)."""
    if source.normalized_url == target.normalized_url:
        return 0.0, {"self_link_penalty": 1.0}, "low"

    kw_a = source_keywords | source.keywords
    kw_b = target_keywords | target.keywords
    keyword_overlap = jaccard_similarity(kw_a, kw_b) if kw_a and kw_b else 0.0

    topic_a = tokenize_query((source_topic or "").casefold())
    topic_b = tokenize_query((target_topic or "").casefold())
    topic_overlap = jaccard_similarity(topic_a, topic_b) if topic_a and topic_b else 0.0
    if source_topic and target_topic and source_topic == target_topic:
        topic_overlap = max(topic_overlap, 0.85)

    title_a = tokenize_query(source.title.casefold())
    title_b = tokenize_query(target.title.casefold())
    title_overlap = jaccard_similarity(title_a, title_b) if title_a and title_b else 0.0

    orphan_value = 0.0
    if target.inbound_count == 0 and target.depth >= 1:
        orphan_value = 0.25
    depth_value = 0.0
    if target.depth >= 4:
        depth_value = min(0.2, target.depth * 0.03)

    existing_penalty = 0.45 if already_linked else 0.0
    reciprocal_penalty = 0.15 if reciprocal else 0.0

    raw = (
        keyword_overlap * 0.35
        + topic_overlap * 0.30
        + title_overlap * 0.15
        + orphan_value
        + depth_value
        - existing_penalty
        - reciprocal_penalty
    )
    score = max(0.0, min(1.0, raw))
    breakdown = {
        "keyword_overlap": round(keyword_overlap, 4),
        "topic_overlap": round(topic_overlap, 4),
        "title_overlap": round(title_overlap, 4),
        "orphan_value": round(orphan_value, 4),
        "depth_value": round(depth_value, 4),
        "existing_link_penalty": round(existing_penalty, 4),
        "reciprocal_penalty": round(reciprocal_penalty, 4),
    }
    if score >= 0.65:
        confidence = "high"
    elif score >= 0.45:
        confidence = "medium"
    else:
        confidence = "low"
    return round(score, 4), breakdown, confidence
