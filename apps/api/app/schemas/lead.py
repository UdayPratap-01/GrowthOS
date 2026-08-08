from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import LeadStatus


class LeadCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr | None = None
    phone: str | None = None
    source: str | None = None
    campaign: str | None = None
    ad: str | None = None
    status: LeadStatus = LeadStatus.new
    notes: str | None = None


class LeadUpdate(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    source: str | None = None
    campaign: str | None = None
    ad: str | None = None
    status: LeadStatus | None = None
    notes: str | None = None


class LeadScoreExplanation(BaseModel):
    score: int
    reasons: list[str]
    based_on_available_data_only: bool
    insufficient_data_note: str | None = None


class LeadOut(BaseModel):
    id: UUID
    client_id: UUID
    name: str
    email: str | None
    phone: str | None
    source: str | None
    campaign: str | None
    ad: str | None
    lead_score: int | None
    score_explanation: dict
    status: LeadStatus
    notes: str | None
    created_at: datetime
    last_activity_at: datetime | None

    model_config = {"from_attributes": True}
