"""
CampaignBuilderAgent — arranges approved concepts into a campaign structure.

Produces the campaign → ad set → ad shape a marketer would build by hand, so the
reviewer sees the actual structure rather than a pile of creatives.

Budget is expressed as `budget_share` (0–1) rather than an amount. The server
multiplies shares by the budget the user entered, so the model can express
allocation strategy without being able to inflate the total. A model that returns
shares summing to 1.4 gets normalised; a model that returned amounts could quietly
propose spending 40% more than was authorised.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.ai.agents.base import BaseAgent
from app.ai.agents.campaign_strategy_agent import GROUNDING_RULES
from app.ai.providers.base import Message
from app.schemas.campaign_generation import CampaignBlueprint
from app.schemas.client import ClientContext


class CampaignBuilderRequest(BaseModel):
    platform: str = "meta"
    platform_label: str = "Meta (Facebook)"
    objective: str = "lead_generation"
    objective_label: str = "Lead Generation"
    optimization: str = "leads"
    placements: list[str] = Field(default_factory=list)
    requested_name: str | None = None
    daily_budget: float | None = None
    currency: str = "USD"
    duration_days: int = 30
    max_ad_sets: int = 3
    #: Concepts with a generated asset are preferred for the first ad in each ad
    #: set, so the reviewer sees a complete ad rather than a copy-only stub.
    concepts: list[dict] = Field(default_factory=list)
    brief: dict = Field(default_factory=dict)
    strategy: dict = Field(default_factory=dict)
    known_data_gaps: list[str] = Field(default_factory=list)


class CampaignBuilderAgent(BaseAgent[CampaignBuilderRequest, CampaignBlueprint]):
    name = "CampaignBuilderAgent"
    output_schema = CampaignBlueprint

    def build_messages(
        self, context: ClientContext, request: CampaignBuilderRequest
    ) -> list[Message]:
        return [
            Message(
                role="system",
                content=(
                    "You are CampaignBuilderAgent for GrowthOS. You lay out a campaign "
                    "structure — ad sets and ads — that a human marketer will review "
                    "before anything is built on a platform.\n\n"
                    f"{GROUNDING_RULES}\n\n"
                    "STRUCTURE:\n"
                    "- Each ad set targets one audience with one optimization goal. "
                    "Splitting the same audience across two ad sets fragments learning "
                    "and is wrong unless the placements genuinely differ.\n"
                    "- `budget_share` is a fraction of the daily budget. Shares across "
                    "all ad sets must sum to 1.0.\n"
                    "- Every ad references a real `concept_id` from the list supplied. "
                    "Do not invent a concept id.\n"
                    "- `ad_set_name` on each ad must exactly match one ad set `name`.\n"
                    "- Use only placements from the supplied list.\n\n"
                    "NEVER: state that this campaign is live, scheduled or published; "
                    "invent a platform campaign id, ad set id or ad id; propose a "
                    "budget larger than the one supplied; predict a result as though "
                    "it were measured."
                ),
            ),
            Message(
                role="user",
                content=(
                    "Build the campaign structure.\n\n"
                    f"Campaign name: {request.requested_name or 'take it from the brief'}\n"
                    f"Platform: {request.platform_label}\n"
                    f"Objective: {request.objective_label}\n"
                    f"Optimization goal for every ad set: {request.optimization}\n"
                    f"Placements available: {request.placements}\n"
                    f"Daily budget to allocate: {request.daily_budget} {request.currency}\n"
                    f"Duration: {request.duration_days} days\n"
                    f"Maximum ad sets: {request.max_ad_sets}\n\n"
                    "Concepts available (use their concept_id values):\n"
                    f"{request.concepts}\n\n"
                    f"Creative brief:\n{request.brief}\n\n"
                    f"Channel and audience strategy:\n"
                    f"{ {k: request.strategy.get(k) for k in ('channel_strategy', 'target_audience', 'positioning')} if isinstance(request.strategy, dict) else '' }\n\n"
                    f"Data gaps to carry into `data_limitations`:\n{request.known_data_gaps}\n\n"
                    f"Client context JSON:\n{context.model_dump_json()}"
                ),
            ),
        ]
