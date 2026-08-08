from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import Priority, RecommendationStatus


class RecommendationCreate(BaseModel):
    client_id: UUID | None = None
    title: str = Field(min_length=1, max_length=255)
    problem: str
    evidence: str
    recommendation: str
    priority: Priority = Priority.medium
    expected_impact: str


class RecommendationStatusUpdate(BaseModel):
    status: RecommendationStatus


class RecommendationOut(BaseModel):
    id: UUID
    organization_id: UUID
    client_id: UUID | None
    client_name: str | None = None
    title: str
    problem: str
    evidence: str
    recommendation: str
    priority: Priority
    expected_impact: str
    status: RecommendationStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RecommendationGenerateRequest(BaseModel):
    client_id: UUID | None = None
