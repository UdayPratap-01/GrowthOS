"""SEO AI recommendation engine (M9.7)."""

from app.seo.recommendations.evidence import build_evidence_snapshot
from app.seo.recommendations.thresholds import ALGORITHM_VERSION, PROMPT_VERSION

__all__ = ["build_evidence_snapshot", "ALGORITHM_VERSION", "PROMPT_VERSION"]
