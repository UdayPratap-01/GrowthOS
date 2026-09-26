"""Structured finding drafts produced by the analysis engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass
class FindingDraft:
    rule_id: str
    category: str
    severity: str
    title: str
    description: str
    evidence: dict[str, Any] = field(default_factory=dict)
    observed_value: str | None = None
    expected_or_heuristic: str | None = None
    recommendation: str | None = None
    url: str | None = None
    page_id: UUID | None = None
    fingerprint: str = "default"

    def dedupe_key(self) -> str:
        return f"{self.rule_id}|{self.url or ''}|{self.fingerprint}"
