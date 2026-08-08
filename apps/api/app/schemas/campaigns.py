from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class CampaignOut(BaseModel):
    id: UUID
    client_id: UUID
    ad_account_id: UUID | None = None
    name: str
    platform: str
    status: str
    objective: str | None = None
    spend: Decimal
    metrics: dict = Field(default_factory=dict)
    data_source: str

    model_config = {"from_attributes": True}
