"""Post-generation evidence grounding validation for SEO recommendations."""

from __future__ import annotations

from app.schemas.seo_recommendation import SeoRecommendationItem, SeoRecommendationsGenerated
from app.seo.recommendations.evidence import EvidenceSnapshot
from app.seo.recommendations.thresholds import EVIDENCE_SOURCES, RECOMMENDATION_TYPES


class GroundingError(Exception):
    def __init__(self, message: str, *, recommendation_title: str | None = None) -> None:
        super().__init__(message)
        self.recommendation_title = recommendation_title


def validate_and_filter_recommendations(
    output: SeoRecommendationsGenerated,
    snapshot: EvidenceSnapshot,
) -> list[SeoRecommendationItem]:
    valid: list[SeoRecommendationItem] = []
    for rec in output.recommendations:
        try:
            _validate_recommendation(rec, snapshot)
            valid.append(rec)
        except GroundingError:
            continue
    return valid


def _validate_recommendation(rec: SeoRecommendationItem, snapshot: EvidenceSnapshot) -> None:
    if rec.type not in RECOMMENDATION_TYPES:
        raise GroundingError(f"invalid recommendation type: {rec.type}", recommendation_title=rec.title)
    if not rec.evidence_refs:
        raise GroundingError("missing evidence_refs", recommendation_title=rec.title)
    for ref in rec.evidence_refs:
        if ref.source not in EVIDENCE_SOURCES:
            raise GroundingError(f"invalid evidence source: {ref.source}", recommendation_title=rec.title)
        allowed = snapshot.index.get(ref.source, set())
        if ref.id not in allowed:
            raise GroundingError(f"evidence id not in snapshot: {ref.source}/{ref.id}", recommendation_title=rec.title)
    for url in rec.affected_urls:
        if url not in snapshot.urls:
            raise GroundingError(f"affected URL not in evidence: {url}", recommendation_title=rec.title)
    for kw in rec.affected_keywords:
        if kw not in snapshot.keywords:
            raise GroundingError(f"affected keyword not in evidence: {kw}", recommendation_title=rec.title)
    for topic_id in rec.affected_topics:
        if topic_id not in snapshot.topic_ids:
            raise GroundingError(f"affected topic not in evidence: {topic_id}", recommendation_title=rec.title)
