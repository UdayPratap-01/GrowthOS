"""Pydantic schemas for SEO approval/actions (M9.13)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import AIActionStatus, AIActionType


class SeoActionProposeRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class SeoActionDecision(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class SeoActionEligibilityOut(BaseModel):
    eligible: bool
    capability: str
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SeoActionOut(BaseModel):
    id: UUID
    organization_id: UUID
    action_type: AIActionType
    status: AIActionStatus
    description: str
    reason: str
    requires_approval: bool
    payload: dict = Field(default_factory=dict)
    evidence: list = Field(default_factory=list)
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    executed_at: datetime | None = None
    result: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SeoActionListOut(BaseModel):
    items: list[SeoActionOut]
    total: int


class SeoActionSummaryOut(BaseModel):
    pending: int
    approved: int
    completed: int
    rejected: int
    failed: int
    review_only: int


class SeoActionAuditOut(BaseModel):
    action_id: UUID
    events: list[dict] = Field(default_factory=list)
