"""SEO schema engine types (M9.11)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EligibilityResult:
    schema_type: str
    status: str
    reasons: list[str] = field(default_factory=list)


@dataclass
class SchemaFindingDraft:
    finding_type: str
    category: str
    severity: str
    level: str
    title: str
    summary: str
    rationale: str
    property_path: str | None = None
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)

    def dedupe_key(self) -> str:
        return f"{self.category}|{self.finding_type}|{self.property_path or ''}|{self.title}"


@dataclass
class GeneratedSchema:
    schema_type: str
    eligibility: EligibilityResult
    json_ld: dict[str, Any] | None
    source_fields: dict[str, str] = field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
