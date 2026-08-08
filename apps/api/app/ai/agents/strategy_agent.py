from pydantic import BaseModel

from app.ai.agents.base import BaseAgent
from app.ai.providers.base import Message
from app.schemas.client import ClientContext
from app.schemas.strategy import StrategyGenerated


class StrategyRequest(BaseModel):
    title: str | None = None


class StrategyAgent(BaseAgent[StrategyRequest, StrategyGenerated]):
    name = "StrategyAgent"
    output_schema = StrategyGenerated

    def build_messages(self, context: ClientContext, request: StrategyRequest) -> list[Message]:
        return [
            Message(
                role="system",
                content=(
                    "You are StrategyAgent for GrowthOS AI. Use only provided client context and metrics. "
                    "Never invent KPIs. If a metric is missing, say Insufficient data."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Generate a marketing strategy.\n"
                    f"Requested title: {request.title or 'Growth Plan'}\n"
                    f"Client context JSON: {context.model_dump_json()}\n"
                    f"Return current situation, problems, opportunities, strategy, and concrete actions."
                ),
            ),
        ]
