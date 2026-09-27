"""On-page optimizer finding drafts (M9.10)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class OnPageFindingDraft:
    finding_type: str
    category: str
    severity: str
    priority: str
    title: str
    summary: str
    rationale: str
    current_value: str | None = None
    expected_value: str | None = None
    recommendation: str = ""
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    affected_section: str | None = None
    affected_element: str | None = None
    suggested_change: str | None = None
    status: str = "open"

    def dedupe_key(self) -> str:
        return f"{self.category}|{self.finding_type}|{self.affected_element or ''}|{self.title}"
