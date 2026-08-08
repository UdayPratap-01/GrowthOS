from pydantic import BaseModel, Field

from app.ai.agents.base import BaseAgent
from app.ai.providers.base import Message
from app.schemas.client import ClientContext


class CompetitorInsightRequest(BaseModel):
    focus: str = "positioning_and_offers"
    competitors: list[str] = Field(default_factory=list)


class CompetitorInsight(BaseModel):
    observations: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    insufficient_data: list[str] = Field(default_factory=list)
    data_label: str = "AI ESTIMATE"


class CompetitorAgent(BaseAgent[CompetitorInsightRequest, CompetitorInsight]):
    name = "CompetitorAgent"
    output_schema = CompetitorInsight

    def build_messages(self, context: ClientContext, request: CompetitorInsightRequest) -> list[Message]:
        names = request.competitors or context.competitors or []
        return [
            Message(
                role="system",
                content=(
                    "You are CompetitorAgent. Use only provided competitor names and client context. "
                    "Label speculative conclusions as AI ESTIMATE. Never invent traffic or ad spend numbers."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Focus: {request.focus}\nCompetitors: {names}\n"
                    f"Client: {context.model_dump_json()}"
                ),
            ),
        ]
