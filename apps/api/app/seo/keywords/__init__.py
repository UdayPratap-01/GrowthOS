"""Keyword opportunity engine (M9.4)."""

from app.seo.keywords.normalize import normalize_query
from app.seo.keywords.rules import detect_keyword_opportunities

__all__ = ["normalize_query", "detect_keyword_opportunities"]
