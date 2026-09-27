"""Internal-link opportunity drafts (M9.12)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass
class PageRecord:
    page_id: UUID | None
    url: str
    normalized_url: str
    title: str
    depth: int
    keywords: set[str] = field(default_factory=set)
    topics: set[str] = field(default_factory=set)
    headings: list[str] = field(default_factory=list)
    inbound_count: int = 0
    outbound_links: set[str] = field(default_factory=set)


@dataclass
class InternalLinkOpportunityDraft:
    source_url: str
    target_url: str
    source_crawl_page_id: UUID | None
    target_crawl_page_id: UUID | None
    anchor_text: str
    anchor_alternatives: list[str]
    opportunity_type: str
    relationship_reason: str
    source_topic: str | None
    target_topic: str | None
    source_keywords: list[str]
    target_keywords: list[str]
    relevance_score: float
    confidence: str
    score_breakdown: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    limitations: list[str] = field(default_factory=list)
    status: str = "suggested"

    def dedupe_key(self) -> str:
        return f"{self.opportunity_type}|{self.source_url}|{self.target_url}|{self.anchor_text[:64]}"
