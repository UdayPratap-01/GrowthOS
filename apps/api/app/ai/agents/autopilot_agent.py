from pydantic import BaseModel, Field

from app.ai.agents.base import BaseAgent
from app.ai.providers.base import Message
from app.schemas.client import ClientContext


class AutopilotPlanRequest(BaseModel):
    goal: str = "Generate Leads"
    budget: float | None = None
    duration_days: int = 30
    platforms: list[str] = Field(default_factory=lambda: ["meta", "instagram"])
    autonomy_mode: str = "assisted"


class AutopilotStep(BaseModel):
    key: str
    label: str
    status: str = "pending"  # pending | running | completed | blocked | skipped
    detail: str | None = None


class AutopilotPlan(BaseModel):
    summary: str
    steps: list[AutopilotStep] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    insufficient_data: list[str] = Field(default_factory=list)
    notes: str = ""


class AutopilotAgent(BaseAgent[AutopilotPlanRequest, AutopilotPlan]):
    name = "AutopilotAgent"
    output_schema = AutopilotPlan

    def build_messages(self, context: ClientContext, request: AutopilotPlanRequest) -> list[Message]:
        return [
            Message(
                role="system",
                content=(
                    "You are AutopilotAgent for GrowthOS. Produce an ordered marketing workflow plan. "
                    "Never claim live publishing or spend without confirmation. "
                    "Include blockers if integrations or credentials are likely missing. "
                    "Steps should cover: analyze client, analyze history, strategy, campaign structure, "
                    "creatives, images, videos, variations, approval, publish, monitor, optimize."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Goal: {request.goal}\nBudget: {request.budget}\nDuration: {request.duration_days}\n"
                    f"Platforms: {request.platforms}\nAutonomy mode: {request.autonomy_mode}\n"
                    f"Client: {context.model_dump_json()}"
                ),
            ),
        ]
