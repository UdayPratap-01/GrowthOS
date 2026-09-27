from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SeoSchemaGenerateRequest(BaseModel):
    schema_types: list[str] | None = Field(default=None, max_length=8)


class SeoSchemaValidateRequest(BaseModel):
    artifact_id: UUID | None = None


class SeoSchemaFindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    artifact_id: UUID
    generated_content_id: UUID
    finding_type: str
    category: str
    severity: str
    level: str
    title: str
    summary: str
    rationale: str
    property_path: str | None
    evidence_refs: list[dict[str, Any]]
    created_at: datetime


class SeoSchemaArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    generated_content_id: UUID
    content_brief_id: UUID
    schema_type: str
    status: str
    json_ld: dict[str, Any] | None
    validation_status: str
    validation_errors: list[dict[str, Any]]
    validation_warnings: list[dict[str, Any]]
    eligibility_status: str
    eligibility_reasons: list[str]
    evidence_refs: list[dict[str, Any]]
    source_fields: dict[str, str]
    limitations: list[str]
    generation_key: str
    generation_algorithm_version: str
    validation_algorithm_version: str
    prompt_version: str
    provider: str
    model: str
    created_at: datetime


class SeoSchemaListOut(BaseModel):
    artifacts: list[SeoSchemaArtifactOut]
    eligibility_summary: list[dict[str, Any]]
    disclaimer: str
