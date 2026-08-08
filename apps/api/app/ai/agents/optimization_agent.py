from pydantic import BaseModel, Field

from app.ai.agents.base import BaseAgent
from app.ai.providers.base import Message
from app.schemas.client import ClientContext


class OptimizationRequest(BaseModel):
    focus: str = "cpl_and_creative_fatigue"
    analytics_summary: dict = Field(default_factory=dict)
    campaigns: list[dict] = Field(default_factory=list)


class OptimizationSuggestion(BaseModel):
    problem: str
    evidence: list[str] = Field(default_factory=list)
    recommendation: str
    priority: str = "medium"
    expected_impact: str
    suggested_action_type: str | None = None
    platform: str | None = None
    target_id: str | None = None
    estimated_cost: float | None = None


class OptimizationPlan(BaseModel):
    summary: str
    suggestions: list[OptimizationSuggestion] = Field(default_factory=list)
    insufficient_data: list[str] = Field(default_factory=list)


class OptimizationAgent(BaseAgent[OptimizationRequest, OptimizationPlan]):
    name = "OptimizationAgent"
    output_schema = OptimizationPlan

    def build_messages(self, context: ClientContext, request: OptimizationRequest) -> list[Message]:
        return [
            Message(
                role="system",
                content=(
                    "You are OptimizationAgent. Detect rising CPL, falling CTR, creative fatigue, overspend. "
                    "Use only provided metrics. Never invent ROAS/CPL. Never guarantee results. "
                    "suggested_action_type must be a valid GrowthOS action like UPDATE_BUDGET, CREATE_CREATIVE, PAUSE_CAMPAIGN."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Focus: {request.focus}\nClient: {context.business_name}\n"
                    f"Analytics: {request.analytics_summary}\nCampaigns: {request.campaigns}\n"
                    f"Available metrics: {context.available_metrics}"
                ),
            ),
        ]
