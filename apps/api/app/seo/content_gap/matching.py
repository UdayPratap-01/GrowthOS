"""Deterministic lexical matching between user topics and competitor pages."""

from __future__ import annotations

from dataclasses import dataclass

from app.seo.content_gap.thresholds import DEFAULT_GAP_THRESHOLDS, ContentGapThresholds
from app.seo.topics.similarity import jaccard_similarity


@dataclass
class TopicMatch:
    user_topic_id: str
    topic_label: str
    representative_query: str
    similarity: float
    match_strength: str
    query_count: int
    page_count: int
    total_impressions: float


def classify_strength(similarity: float, thresholds: ContentGapThresholds = DEFAULT_GAP_THRESHOLDS) -> str:
    if similarity >= thresholds.strong_match:
        return "strong_match"
    if similarity >= thresholds.moderate_match:
        return "moderate_match"
    if similarity >= thresholds.weak_match:
        return "weak_match"
    return "no_match"


def best_topic_match(
    competitor_tokens: set[str],
    user_topics: list[dict],
    *,
    thresholds: ContentGapThresholds = DEFAULT_GAP_THRESHOLDS,
) -> TopicMatch | None:
    best: TopicMatch | None = None
    for topic in user_topics:
        tokens = topic.get("tokens") or set()
        sim = jaccard_similarity(competitor_tokens, tokens)
        strength = classify_strength(sim, thresholds)
        if strength == "no_match":
            continue
        candidate = TopicMatch(
            user_topic_id=str(topic["id"]),
            topic_label=str(topic.get("topic_label") or ""),
            representative_query=str(topic.get("representative_query") or ""),
            similarity=round(sim, 4),
            match_strength=strength,
            query_count=int(topic.get("query_count") or 0),
            page_count=int(topic.get("page_count") or 0),
            total_impressions=float(topic.get("total_impressions") or 0),
        )
        if best is None or candidate.similarity > best.similarity:
            best = candidate
    return best
