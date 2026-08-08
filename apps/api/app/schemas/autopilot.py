from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import AIActionStatus, AIActionType, AutonomyMode, Priority, RiskLevel


class AutonomySettingsOut(BaseModel):
    id: UUID
    organization_id: UUID
    client_id: UUID | None = None
    autonomy_mode: AutonomyMode
    maximum_daily_ad_spend: Decimal
    maximum_campaign_budget: Decimal
    maximum_budget_increase_percentage: Decimal
    maximum_budget_decrease_percentage: Decimal
    maximum_campaigns_per_day: int
    maximum_creatives_per_day: int
    maximum_posts_per_day: int
    maximum_actions_per_day: int = 50
    require_approval_for_financial_actions: bool
    require_approval_for_publishing: bool
    require_approval_for_campaign_creation: bool
    allowed_platforms: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    automation_enabled: bool
    max_ai_iterations: int = 1
    max_ai_actions_per_cycle: int = 5
    max_execution_time: int = 300
    max_failures_per_cycle: int = 3

    model_config = {"from_attributes": True}


class AutonomySettingsUpdate(BaseModel):
    autonomy_mode: AutonomyMode | None = None
    maximum_daily_ad_spend: Decimal | None = None
    maximum_campaign_budget: Decimal | None = None
    maximum_budget_increase_percentage: Decimal | None = None
    maximum_budget_decrease_percentage: Decimal | None = None
    maximum_campaigns_per_day: int | None = Field(default=None, ge=0, le=100)
    maximum_creatives_per_day: int | None = Field(default=None, ge=0, le=200)
    maximum_posts_per_day: int | None = Field(default=None, ge=0, le=100)
    maximum_actions_per_day: int | None = Field(default=None, ge=0, le=500)
    require_approval_for_financial_actions: bool | None = None
    require_approval_for_publishing: bool | None = None
    require_approval_for_campaign_creation: bool | None = None
    allowed_platforms: list[str] | None = None
    allowed_actions: list[str] | None = None
    automation_enabled: bool | None = None
    max_ai_iterations: int | None = Field(default=None, ge=1, le=10)
    max_ai_actions_per_cycle: int | None = Field(default=None, ge=1, le=50)
    max_execution_time: int | None = Field(default=None, ge=30, le=3600)
    max_failures_per_cycle: int | None = Field(default=None, ge=1, le=20)


class AIActionCreate(BaseModel):
    action_type: AIActionType
    client_id: UUID | None = None
    agent: str = "orchestrator"
    platform: str | None = None
    target_id: str | None = None
    description: str
    reason: str
    evidence: list = Field(default_factory=list)
    expected_impact: str | None = None
    estimated_cost: Decimal | None = None
    risk_level: RiskLevel | None = None
    priority: Priority = Priority.medium
    payload: dict = Field(default_factory=dict)
    demo_mode: bool | None = None


class AIActionOut(BaseModel):
    id: UUID
    organization_id: UUID
    client_id: UUID | None
    action_type: AIActionType
    agent: str
    platform: str | None
    target_id: str | None
    description: str
    reason: str
    evidence: list
    expected_impact: str | None
    estimated_cost: Decimal | None
    risk_level: RiskLevel
    priority: Priority
    requires_approval: bool
    status: AIActionStatus
    payload: dict
    previous_state: dict
    result: dict
    demo_mode: bool
    expires_at: datetime | None
    approved_by: UUID | None
    approved_at: datetime | None
    executed_at: datetime | None
    error: str | None
    retry_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ActionDecision(BaseModel):
    note: str | None = None


class AutopilotSummary(BaseModel):
    autonomy_mode: AutonomyMode
    automation_enabled: bool
    pending_approvals: int
    executing: int
    completed_today: int
    failed_today: int
    scheduled_posts: int
    creatives_generated: int
    optimizations_open: int
    campaigns_monitored: int
    demo_mode: bool


class OptimizationRuleIn(BaseModel):
    name: str
    enabled: bool = True
    client_id: UUID | None = None
    condition: dict = Field(default_factory=dict)
    action_template: dict = Field(default_factory=dict)
    priority: Priority = Priority.medium


class OptimizationRuleOut(BaseModel):
    id: UUID
    organization_id: UUID
    client_id: UUID | None
    name: str
    enabled: bool
    condition: dict
    action_template: dict
    priority: Priority

    model_config = {"from_attributes": True}


class OptimizationEventOut(BaseModel):
    id: UUID
    organization_id: UUID
    client_id: UUID | None
    rule_id: UUID | None
    action_id: UUID | None
    problem: str
    evidence: list
    recommendation: str
    priority: Priority
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class CampaignHealthOut(BaseModel):
    id: UUID
    campaign_id: UUID
    client_id: UUID
    score: int
    category: str
    evidence: list
    metrics_snapshot: dict
    data_source: str

    model_config = {"from_attributes": True}


class CreativeGenerateRequest(BaseModel):
    client_id: UUID
    platform: str = "instagram"
    objective: str = "Lead generation"
    format: str = "Reel"
    topic: str | None = None
    count: int = Field(default=3, ge=1, le=10)


class CreativeAssetOut(BaseModel):
    id: UUID
    client_id: UUID
    campaign_id: UUID | None
    name: str
    asset_type: str
    platform: str | None
    prompt: str | None
    provider: str | None
    status: str
    content: dict
    meta: dict
    data_source: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ImageGenerateRequest(BaseModel):
    client_id: UUID
    prompt: str
    platform: str | None = None


class VideoGenerateRequest(BaseModel):
    client_id: UUID
    prompt: str
    platform: str | None = None


class ScheduleContentRequest(BaseModel):
    client_id: UUID
    platform: str
    scheduled_for: datetime
    content: dict = Field(default_factory=dict)
    description: str = "Schedule content"


class PublishContentRequest(BaseModel):
    client_id: UUID
    platform: str
    content: dict = Field(default_factory=dict)
    description: str = "Publish content"


class CampaignProposeRequest(BaseModel):
    client_id: UUID
    platform: str = "meta"
    name: str
    objective: str = "leads"
    daily_budget: Decimal = Decimal("50")
    reason: str = "Campaign proposal from Autopilot"


class DecisionLoopRequest(BaseModel):
    client_id: UUID
    max_actions: int = Field(default=5, ge=1, le=20)
    max_iterations: int = Field(default=1, ge=1, le=3)


class DecisionLoopResult(BaseModel):
    actions_created: int
    events: list[OptimizationEventOut] = Field(default_factory=list)
    message: str


class AssistantCommandResult(BaseModel):
    reply: str
    actions: list[AIActionOut] = Field(default_factory=list)


class CampaignBuildRequest(BaseModel):
    client_id: UUID
    objective: str = "Generate Leads"
    budget: Decimal | None = Decimal("500")
    duration_days: int = Field(default=30, ge=1, le=365)
    offer: str | None = None
    target_audience: str | None = None
    platforms: list[str] = Field(default_factory=lambda: ["meta", "instagram"])
    location: str | None = None
    image_quantity: int = Field(default=5, ge=0, le=20)
    video_quantity: int = Field(default=3, ge=0, le=10)
    variation_quantity: int = Field(default=10, ge=0, le=40)
    campaign_goal: str | None = None
    cta: str | None = None


class AutopilotRunRequest(BaseModel):
    client_id: UUID
    goal: str = "Generate Leads"
    budget: Decimal | None = Decimal("500")
    duration_days: int = Field(default=30, ge=1, le=365)
    platforms: list[str] = Field(default_factory=lambda: ["meta", "instagram"])
    autonomy_mode: str | None = None
    offer: str | None = None
    target_audience: str | None = None
    image_quantity: int = Field(default=5, ge=0, le=20)
    video_quantity: int = Field(default=3, ge=0, le=10)
    variation_quantity: int = Field(default=10, ge=0, le=40)
    cta: str | None = None


class AutopilotRunStep(BaseModel):
    key: str
    label: str
    status: str
    detail: str | None = None


class AutopilotRunOut(BaseModel):
    id: UUID
    organization_id: UUID
    client_id: UUID
    run_type: str
    status: str
    goal: str
    budget: Decimal | None
    duration_days: int
    platforms: list
    autonomy_mode: str | None
    steps: list
    action_ids: list
    result: dict
    error: str | None
    demo_mode: bool
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class CampaignBuildResult(BaseModel):
    run: AutopilotRunOut
    action_ids: list[str] = Field(default_factory=list)
    plan: dict = Field(default_factory=dict)
    message: str


class CreativeVariationsRequest(BaseModel):
    client_id: UUID
    platform: str = "instagram"
    format: str = "Reel"
    objective: str = "Lead generation"
    topic: str | None = None
    count: int = Field(default=5, ge=1, le=20)
