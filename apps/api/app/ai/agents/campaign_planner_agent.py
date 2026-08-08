from pydantic import BaseModel, Field

from app.ai.agents.base import BaseAgent
from app.ai.providers.base import Message
from app.schemas.client import ClientContext


class CampaignPlanRequest(BaseModel):
    objective: str = "Generate Leads"
    budget: float | None = None
    duration_days: int = 30
    offer: str | None = None
    audience: str | None = None
    platforms: list[str] = Field(default_factory=lambda: ["meta", "instagram"])
    location: str | None = None
    image_quantity: int = 5
    video_quantity: int = 3
    variation_quantity: int = 10
    cta: str | None = None
    campaign_goal: str | None = None


class AdSetPlan(BaseModel):
    name: str
    audience: str
    placement: str
    daily_budget_share: float
    optimization: str


class AdPlan(BaseModel):
    name: str
    headline: str
    primary_text: str
    cta: str
    creative_type: str
    destination: str | None = None


class CampaignStructure(BaseModel):
    name: str
    objective: str
    platforms: list[str] = Field(default_factory=list)
    total_budget: float | None = None
    duration_days: int = 30
    messaging_strategy: str
    audience_strategy: str
    creative_concepts: list[str] = Field(default_factory=list)
    image_prompts: list[str] = Field(default_factory=list)
    video_scripts: list[str] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)
    headlines: list[str] = Field(default_factory=list)
    primary_texts: list[str] = Field(default_factory=list)
    ctas: list[str] = Field(default_factory=list)
    ad_sets: list[AdSetPlan] = Field(default_factory=list)
    ads: list[AdPlan] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    insufficient_data: list[str] = Field(default_factory=list)
    notes: str = ""


class CampaignPlannerAgent(BaseAgent[CampaignPlanRequest, CampaignStructure]):
    name = "CampaignPlannerAgent"
    output_schema = CampaignStructure

    def build_messages(self, context: ClientContext, request: CampaignPlanRequest) -> list[Message]:
        return [
            Message(
                role="system",
                content=(
                    "You are CampaignPlannerAgent for GrowthOS. Build a structured campaign plan only. "
                    "Never invent live metrics, spend, ROAS, or platform IDs. "
                    "If client data is thin, list Insufficient data fields. "
                    "Do not claim campaigns will be published — plan only."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Objective: {request.objective}\n"
                    f"Budget: {request.budget}\nDuration days: {request.duration_days}\n"
                    f"Offer: {request.offer}\nAudience: {request.audience or context.target_audience}\n"
                    f"Platforms: {request.platforms}\nLocation: {request.location or context.location}\n"
                    f"Images: {request.image_quantity} Videos: {request.video_quantity} "
                    f"Variations: {request.variation_quantity}\n"
                    f"CTA: {request.cta}\nGoal: {request.campaign_goal or request.objective}\n"
                    f"Client: {context.model_dump_json()}"
                ),
            ),
        ]
