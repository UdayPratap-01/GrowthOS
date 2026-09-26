"""Deterministic Search Console query normalization."""

from __future__ import annotations

import re
import unicodedata


def normalize_query(query: str | None) -> str | None:
    """Return normalized form for matching; preserve original separately for display."""
    if query is None:
        return None
    text = unicodedata.normalize("NFKC", str(query))
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return None
    return text.casefold()


def is_long_tail(query: str, *, min_tokens: int = 4) -> bool:
    """Structural long-tail signal based on token count — not commercial intent."""
    tokens = [t for t in re.split(r"\s+", query.strip()) if t]
    return len(tokens) >= min_tokens
