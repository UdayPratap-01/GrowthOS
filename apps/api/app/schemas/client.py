from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class ClientBase(BaseModel):
    business_name: str = Field(min_length=1, max_length=255)
    industry: str | None = None
    website: str | None = None
    description: str | None = None
    location: str | None = None
    target_audience: str | None = None
    products_services: str | None = None
    marketing_goals: str | None = None
    monthly_budget: Decimal | None = None
    brand_voice: str | None = None
    competitors: list[str] = Field(default_factory=list)
    primary_channels: list[str] = Field(default_factory=list)
    kpis: list[str] = Field(default_factory=list)


class ClientCreate(ClientBase):
    pass


class ClientUpdate(BaseModel):
    business_name: str | None = None
    industry: str | None = None
    website: str | None = None
    description: str | None = None
    location: str | None = None
    target_audience: str | None = None
    products_services: str | None = None
    marketing_goals: str | None = None
    monthly_budget: Decimal | None = None
    brand_voice: str | None = None
    competitors: list[str] | None = None
    primary_channels: list[str] | None = None
    kpis: list[str] | None = None


class ClientOut(ClientBase):
    id: UUID
    organization_id: UUID
    status: str
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ClientContext(BaseModel):
    """
    Structured client context supplied to AI agents.

    `available_metrics` holds only figures that were actually recorded;
    `insufficient_data_fields` names what is missing. Together they are the
    contract that lets a prompt say "use these numbers and no others".

    The history fields below are populated only by the campaign engine's richer
    builder (`app.campaigns.context`). Other callers leave them empty rather than
    paying to assemble history that a lead-scoring or content prompt would not
    use — and an empty list here means "not loaded or not present", never
    "performed poorly".
    """

    client_id: UUID
    organization_id: UUID
    business_name: str
    industry: str | None
    website: str | None
    description: str | None
    location: str | None
    target_audience: str | None
    products_services: str | None
    marketing_goals: str | None
    monthly_budget: Decimal | None
    brand_voice: str | None
    competitors: list[str]
    primary_channels: list[str]
    kpis: list[str]
    demo_mode: bool
    available_metrics: dict
    insufficient_data_fields: list[str] = Field(default_factory=list)
    historical_campaign_performance: list[dict] = Field(default_factory=list)
    historical_content_performance: list[dict] = Field(default_factory=list)
    lead_performance: dict = Field(default_factory=dict)
    previous_strategies: list[dict] = Field(default_factory=list)
