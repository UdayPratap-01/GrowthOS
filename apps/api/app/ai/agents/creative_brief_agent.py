"""
CreativeBriefAgent — turns strategy into the brief the creative agents work from.

Sits between strategy and copy so that copy, concepts and variations all read the
*same* brief. Without it each downstream agent would re-derive the audience and
value proposition from raw context and drift apart, which is how a campaign ends
up with three concepts that address three different customers by accident.

Budget is not in the output schema: it is a commercial commitment that comes from
the request, and a model-chosen budget would be an invented spend decision.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.ai.agents.base import BaseAgent
from app.ai.agents.campaign_strategy_agent import GROUNDING_RULES
from app.ai.providers.base import Message
from app.schemas.campaign_generation import CampaignBriefDraft
from app.schemas.client import ClientContext


class CreativeBriefRequest(BaseModel):
    platform: str = "meta"
    platform_label: str = "Meta (Facebook)"
    objective: str = "lead_generation"
    objective_label: str = "Lead Generation"
    offer: str | None = None
    audience: str | None = None
    tone: str | None = None
    cta: str | None = None
    requested_name: str | None = None
    #: The strategy document, passed as data so the brief is derived from it
    #: rather than from a second independent reading of the client record.
    strategy: dict = Field(default_factory=dict)
    known_data_gaps: list[str] = Field(default_factory=list)


class CreativeBriefAgent(BaseAgent[CreativeBriefRequest, CampaignBriefDraft]):
    name = "CreativeBriefAgent"
    output_schema = CampaignBriefDraft

    def build_messages(
        self, context: ClientContext, request: CreativeBriefRequest
    ) -> list[Message]:
        return [
            Message(
                role="system",
                content=(
                    "You are CreativeBriefAgent for GrowthOS. You convert an approved "
                    "campaign strategy into a tight creative brief that copywriters "
                    "and art directors can execute from without asking questions.\n\n"
                    f"{GROUNDING_RULES}\n\n"
                    "`pain_points` must be problems this specific audience plausibly "
                    "has given the client's products and market — not generic "
                    "business complaints. `brand_constraints` records what the "
                    "creative must not do, drawn from the client's brand voice and "
                    "industry: regulatory limits, claims that cannot be substantiated, "
                    "tone the brand avoids. `campaign_name` should be short, specific "
                    "and readable in a campaign list — no emoji, no invented dates."
                ),
            ),
            Message(
                role="user",
                content=(
                    "Write the creative brief.\n\n"
                    f"Platform: {request.platform_label}\n"
                    f"Objective: {request.objective_label}\n"
                    f"Offer: {request.offer or 'Not supplied'}\n"
                    f"Audience brief: {request.audience or context.target_audience or 'Not supplied'}\n"
                    f"Requested tone: {request.tone or context.brand_voice or 'Not supplied'}\n"
                    f"Requested CTA: {request.cta or 'Choose one that fits the objective'}\n"
                    f"Requested campaign name: {request.requested_name or 'Propose one'}\n\n"
                    f"Approved strategy:\n{request.strategy}\n\n"
                    f"Data gaps to carry into `data_limitations`:\n{request.known_data_gaps}\n\n"
                    f"Client context JSON:\n{context.model_dump_json()}"
                ),
            ),
        ]
