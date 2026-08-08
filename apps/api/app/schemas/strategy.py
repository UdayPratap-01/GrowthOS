from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import ActionStatus, Priority


class StrategyActionOut(BaseModel):
    id: UUID
    action: str
    channel: str
    objective: str
    priority: Priority
    estimated_effort: str
    expected_outcome: str
    required_assets: list[str]
    deadline: date | None
    status: ActionStatus

    model_config = {"from_attributes": True}


class StrategyOut(BaseModel):
    id: UUID
    client_id: UUID
    title: str
    current_situation: str
    what_is_happening: str
    key_problems: list[str]
    opportunities: list[str]
    strategy_summary: str
    status: str
    source: str
    actions: list[StrategyActionOut]
    created_at: datetime

    model_config = {"from_attributes": True}


class StrategyGenerateRequest(BaseModel):
    title: str | None = None


class ActionStatusUpdate(BaseModel):
    status: ActionStatus


class StrategyActionGenerated(BaseModel):
    action: str
    channel: str
    objective: str
    priority: Priority = Priority.medium
    estimated_effort: str = "medium"
    expected_outcome: str
    required_assets: list[str] = Field(default_factory=list)
    deadline: date | None = None


class StrategyGenerated(BaseModel):
    title: str
    current_situation: str
    what_is_happening: str
    key_problems: list[str]
    opportunities: list[str]
    strategy_summary: str
    actions: list[StrategyActionGenerated]
