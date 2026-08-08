from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ReportGenerateRequest(BaseModel):
    period_days: int = Field(default=7, ge=7, le=90)


class ReportOut(BaseModel):
    id: UUID
    client_id: UUID
    title: str
    period_start: date
    period_end: date
    content: dict
    export_path: str | None
    status: str
    created_at: datetime
    data_source: str | None = None

    model_config = {"from_attributes": True}
