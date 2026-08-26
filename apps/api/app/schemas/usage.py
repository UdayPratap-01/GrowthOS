from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class UsageSummaryOut(BaseModel):
    organization_id: UUID
    period: str
    #: Metric name to quantity. Consumption only — no prices, by design.
    totals: dict[str, float]


class UsageRecordOut(BaseModel):
    id: UUID
    metric: str
    quantity: float
    period: str
    occurred_at: datetime
    client_id: UUID | None = None
    details: dict = {}

    model_config = {"from_attributes": True}
