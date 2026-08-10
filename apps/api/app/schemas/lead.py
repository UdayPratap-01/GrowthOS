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
    """
    A lead score and the exact basis for it.

    `method` states how the score was produced so the UI never implies an LLM
    was involved when it was not.
    """

    score: int = Field(ge=0, le=100)
    # deterministic_rules — a transparent rule engine, not an LLM.
    method: str = "deterministic_rules"
    method_label: str = "Deterministic rule-based scoring"
    reasons: list[str]
    # The concrete field values the score was computed from. Never inferred behaviour.
    evidence: list[str] = Field(default_factory=list)
    # What could not be assessed because the data does not exist.
    data_limitations: list[str] = Field(default_factory=list)
    based_on_available_data_only: bool = True
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
