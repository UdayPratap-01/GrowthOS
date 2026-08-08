from pydantic import BaseModel, Field

from app.ai.agents.base import BaseAgent
from app.ai.providers.base import Message
from app.schemas.client import ClientContext


class MonitoringRequest(BaseModel):
    campaigns: list[dict] = Field(default_factory=list)
    analytics_summary: dict = Field(default_factory=dict)


class CampaignHealthDraft(BaseModel):
    campaign_id: str | None = None
    campaign_name: str
    score: int = Field(ge=0, le=100)
    category: str
    evidence: list[str] = Field(default_factory=list)


class MonitoringReport(BaseModel):
    overview: str
    health: list[CampaignHealthDraft] = Field(default_factory=list)
    alerts: list[str] = Field(default_factory=list)
    insufficient_data: list[str] = Field(default_factory=list)


class MonitoringAgent(BaseAgent[MonitoringRequest, MonitoringReport]):
    name = "MonitoringAgent"
    output_schema = MonitoringReport

    def build_messages(self, context: ClientContext, request: MonitoringRequest) -> list[Message]:
        return [
            Message(
                role="system",
                content=(
                    "You are MonitoringAgent. Score campaign health 0-100 using only provided metrics. "
                    "Categories: excellent, good, needs_attention, poor, critical. "
                    "If metrics missing, say Insufficient data. Never invent numbers."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Client: {context.business_name}\nAnalytics: {request.analytics_summary}\n"
                    f"Campaigns: {request.campaigns}"
                ),
            ),
        ]
