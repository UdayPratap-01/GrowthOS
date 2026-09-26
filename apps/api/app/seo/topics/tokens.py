"""Deterministic query tokenization for topic clustering."""

from __future__ import annotations

import re

# Minimal deterministic stop words — conservative to preserve SEO terms.
STOP_WORDS = frozenset({"a", "an", "the", "and", "or", "of", "in", "on", "for", "to", "is"})


def tokenize_query(normalized_query: str, *, remove_stop_words: bool = False) -> set[str]:
    """Tokenize a normalized (casefold) query into a deterministic token set."""
    tokens = re.findall(r"[\w\u0080-\uFFFF]+", normalized_query, flags=re.UNICODE)
    tokens = [t for t in tokens if len(t) >= 1]
    if remove_stop_words:
        tokens = [t for t in tokens if t not in STOP_WORDS]
    if not tokens:
        tokens = normalized_query.split()
    return set(tokens)


def bigrams(tokens: set[str]) -> set[str]:
    ordered = sorted(tokens)
    return {f"{ordered[i]} {ordered[i+1]}" for i in range(len(ordered) - 1)} if len(ordered) > 1 else set()
