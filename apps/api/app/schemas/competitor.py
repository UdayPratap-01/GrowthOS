from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CompetitorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    url: str | None = None
    notes: str | None = None
    observations: dict = Field(default_factory=dict)


class CompetitorUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    notes: str | None = None
    observations: dict | None = None


class CompetitorOut(BaseModel):
    id: UUID
    client_id: UUID
    name: str
    url: str | None
    notes: str | None
    observations: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
