from pydantic import BaseModel, Field

from app.ai.agents.base import BaseAgent
from app.ai.providers.base import Message
from app.schemas.client import ClientContext


class AnalyticsInsightRequest(BaseModel):
    question: str = "Summarize performance"


class AnalyticsInsight(BaseModel):
    summary: str
    findings: list[str] = Field(default_factory=list)
    insufficient_data: list[str] = Field(default_factory=list)


class AnalyticsAgent(BaseAgent[AnalyticsInsightRequest, AnalyticsInsight]):
    name = "AnalyticsAgent"
    output_schema = AnalyticsInsight

    def build_messages(self, context: ClientContext, request: AnalyticsInsightRequest) -> list[Message]:
        metrics = context.available_metrics
        return [
            Message(
                role="system",
                content=(
                    "You are AnalyticsAgent. Interpret only provided metrics. "
                    "Never invent numbers. Explicitly list Insufficient data gaps."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Question: {request.question}\n"
                    f"Metrics: {metrics}\n"
                    f"Insufficient fields: {context.insufficient_data_fields}"
                ),
            ),
        ]
