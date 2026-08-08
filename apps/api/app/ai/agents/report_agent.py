from pydantic import BaseModel, Field

from app.ai.agents.base import BaseAgent
from app.ai.providers.base import Message
from app.schemas.client import ClientContext


class ReportRequest(BaseModel):
    period_label: str = "This week"


class WeeklyReportDraft(BaseModel):
    executive_summary: str
    key_metrics: list[str] = Field(default_factory=list)
    growth: list[str] = Field(default_factory=list)
    declines: list[str] = Field(default_factory=list)
    insights: list[str] = Field(default_factory=list)
    next_week_strategy: str
    insufficient_data: list[str] = Field(default_factory=list)


class ReportAgent(BaseAgent[ReportRequest, WeeklyReportDraft]):
    name = "ReportAgent"
    output_schema = WeeklyReportDraft

    def build_messages(self, context: ClientContext, request: ReportRequest) -> list[Message]:
        return [
            Message(
                role="system",
                content=(
                    "You are ReportAgent. Build weekly report sections using only available metrics. "
                    "Never invent performance data."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Period: {request.period_label}\n"
                    f"Client: {context.business_name}\n"
                    f"Metrics: {context.available_metrics}\n"
                    f"Gaps: {context.insufficient_data_fields}"
                ),
            ),
        ]
