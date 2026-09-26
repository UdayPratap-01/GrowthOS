"""Content-gap analysis engine (M9.6)."""

from app.seo.content_gap.engine import detect_content_gaps
from app.seo.content_gap.thresholds import ALGORITHM_VERSION

__all__ = ["detect_content_gaps", "ALGORITHM_VERSION"]
