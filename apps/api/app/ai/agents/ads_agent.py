from pydantic import BaseModel, Field

from app.ai.agents.base import BaseAgent
from app.ai.providers.base import Message
from app.schemas.client import ClientContext


class AdsInsightRequest(BaseModel):
    focus: str = "efficiency"


class AdsInsight(BaseModel):
    summary: str
    recommendations: list[str] = Field(default_factory=list)
    insufficient_data: list[str] = Field(default_factory=list)


class AdsAgent(BaseAgent[AdsInsightRequest, AdsInsight]):
    name = "AdsAgent"
    output_schema = AdsInsight

    def build_messages(self, context: ClientContext, request: AdsInsightRequest) -> list[Message]:
        return [
            Message(
                role="system",
                content="You are AdsAgent. Use only campaign metrics provided. Never invent ROAS/CPL.",
            ),
            Message(
                role="user",
                content=(
                    f"Focus: {request.focus}\n"
                    f"Client: {context.business_name}\n"
                    f"Metrics: {context.available_metrics}\n"
                    f"Channels: {context.primary_channels}"
                ),
            ),
        ]
