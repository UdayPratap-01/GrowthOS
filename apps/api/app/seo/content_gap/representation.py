"""Deterministic competitor page topic representation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.seo.keywords.normalize import normalize_query
from app.seo.topics.tokens import tokenize_query


@dataclass
class PageRepresentation:
    url: str
    title: str | None
    h1: str | None
    headings: list[str]
    topic_label: str
    tokens: set[str]
    word_count: int


def extract_page_representation(*, url: str, observations: dict) -> PageRepresentation:
    title = (observations.get("title") or "").strip() or None
    h1_list = observations.get("h1_text") or []
    h2_list = observations.get("h2_text") or []
    h1 = h1_list[0].strip() if h1_list else None
    headings = [h.strip() for h in (h1_list + h2_list) if h and h.strip()][:15]

    parts = [p for p in [title, h1, *headings] if p]
    combined = normalize_query(" ".join(parts))
    tokens = tokenize_query(combined) if combined else set()
    topic_label = h1 or title or url
    word_count = len(re.findall(r"[\w\u0080-\uFFFF]+", combined, flags=re.UNICODE)) if combined else 0

    return PageRepresentation(
        url=url,
        title=title,
        h1=h1,
        headings=headings,
        topic_label=topic_label[:512],
        tokens=tokens,
        word_count=word_count,
    )


def user_topic_tokens(*, topic_label: str, representative_query: str, queries: list[str]) -> set[str]:
    combined = normalize_query(" ".join([topic_label, representative_query, *queries[:5]]))
    return tokenize_query(combined) if combined else set()
