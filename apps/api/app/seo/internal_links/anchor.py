"""Deterministic anchor-text generation (M9.12)."""

from __future__ import annotations

import re

from app.seo.internal_links.types import PageRecord

GENERIC_ANCHORS = frozenset({"click here", "read more", "learn more", "here", "link"})


def generate_anchor_text(
    *,
    target: PageRecord,
    target_keywords: list[str],
    target_topic: str | None,
) -> tuple[str, list[str], list[str]]:
    """Return (primary_anchor, alternatives, limitations)."""
    limitations: list[str] = []
    candidates: list[str] = []

    title = (target.title or "").strip()
    if title and len(title) <= 80 and title.lower() not in GENERIC_ANCHORS:
        candidates.append(title)

    for heading in target.headings[:5]:
        h = heading.strip()
        if h and len(h) <= 80 and h.lower() not in GENERIC_ANCHORS:
            candidates.append(h)

    for kw in target_keywords[:3]:
        kw = kw.strip()
        if kw and len(kw) <= 60:
            candidates.append(kw)

    if target_topic and len(target_topic) <= 60:
        candidates.append(target_topic)

    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        key = c.casefold()
        if key not in seen and key not in GENERIC_ANCHORS:
            seen.add(key)
            unique.append(c)

    if not unique:
        limitations.append("Could not derive a reliable anchor from crawl evidence; target title/headings unavailable.")
        return "", [], limitations

    primary = unique[0]
    alts = unique[1:4]
    if _looks_stuffed(primary, target_keywords):
        limitations.append("Primary anchor may be keyword-heavy; review before use.")
    return primary, alts, limitations


def _looks_stuffed(anchor: str, keywords: list[str]) -> bool:
    if not keywords:
        return False
    words = set(re.findall(r"\b\w+\b", anchor.lower()))
    kw_words = set()
    for kw in keywords:
        kw_words.update(re.findall(r"\b\w+\b", kw.lower()))
    if not kw_words:
        return False
    overlap = len(words & kw_words) / max(len(words), 1)
    return overlap > 0.8 and len(words) <= 4
