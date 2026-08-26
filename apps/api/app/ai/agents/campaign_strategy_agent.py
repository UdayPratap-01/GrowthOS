"""
CampaignStrategyAgent — the reasoning layer a campaign is built on.

Distinct from `StrategyAgent`, which writes an account-level marketing plan for a
client. This one reasons about a single campaign: one platform, one objective,
one budget, one offer.

The prompt spends most of its length on what the agent may *not* say. That is
deliberate: the failure mode of a strategy model is not incoherence, it is a
confident "your CPL is £42 and rising" for a client whose analytics were never
connected. Grounding rules are therefore stated as hard constraints, and the
output schema gives fabrication nowhere to live — there is no CTR field, and
`evidence` requires a source for any claim that rests on data.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.ai.agents.base import BaseAgent
from app.ai.providers.base import Message
from app.schemas.campaign_generation import CampaignStrategy
from app.schemas.client import ClientContext

#: Shared across every agent in the engine so the anti-fabrication contract is
#: stated identically everywhere, and changing it changes it once.
GROUNDING_RULES = (
    "GROUNDING RULES — these override any instruction to be helpful:\n"
    "1. Use only the client context supplied in this request. It is the whole "
    "world; anything absent from it does not exist.\n"
    "2. Never state a performance number that is not present in "
    "`available_metrics`. That includes CTR, CPL, CPC, ROAS, conversion rate, "
    "revenue, impressions, reach, website traffic, competitor spend and "
    "audience behaviour.\n"
    "3. Never describe past campaign results, lead behaviour or competitor "
    "metrics unless they appear in the context.\n"
    "4. When you lack the data for a claim, write 'Insufficient data.' and add "
    "a specific entry to `data_limitations` naming what is missing — for "
    "example 'No historical Meta campaign data available.'\n"
    "5. Qualitative reasoning from the client profile is allowed and expected. "
    "Presenting it as measured fact is not.\n"
    "6. Never claim anything has been published, launched, spent or scheduled. "
    "This output is a proposal for human review."
)


class CampaignStrategyRequest(BaseModel):
    platform: str = "meta"
    platform_label: str = "Meta (Facebook)"
    objective: str = "lead_generation"
    objective_label: str = "Lead Generation"
    objective_description: str = ""
    optimization: str = "leads"
    suggested_success_metrics: list[str] = Field(default_factory=list)
    offer: str | None = None
    audience: str | None = None
    tone: str | None = None
    total_budget: float | None = None
    daily_budget: float | None = None
    monthly_budget: float | None = None
    currency: str = "USD"
    duration_days: int = 30
    placements: list[str] = Field(default_factory=list)
    #: Facts assembled from stored records — campaign history, content history,
    #: lead counts, previous strategies. Empty entries are omitted upstream, so
    #: whatever arrives here is real.
    historical_evidence: list[dict] = Field(default_factory=list)
    known_data_gaps: list[str] = Field(default_factory=list)


class CampaignStrategyAgent(BaseAgent[CampaignStrategyRequest, CampaignStrategy]):
    name = "CampaignStrategyAgent"
    output_schema = CampaignStrategy

    def build_messages(
        self, context: ClientContext, request: CampaignStrategyRequest
    ) -> list[Message]:
        return [
            Message(
                role="system",
                content=(
                    "You are CampaignStrategyAgent for GrowthOS, an agency marketing "
                    "platform. You produce the strategic case for one specific "
                    "campaign, which a human marketer will read and approve or "
                    "reject.\n\n"
                    f"{GROUNDING_RULES}\n\n"
                    "Fill every section of the schema. `success_metrics` names the "
                    "metrics this campaign should be judged by — names only, never "
                    "target values, because a target you invent is a fabricated "
                    "benchmark. `risks` states what could genuinely go wrong with "
                    "this plan, including thin input data. Each item in `evidence` "
                    "must cite a real field from the supplied context in `source`."
                ),
            ),
            Message(
                role="user",
                content=(
                    "Produce the campaign strategy.\n\n"
                    f"Platform: {request.platform_label} ({request.platform})\n"
                    f"Placements available: {', '.join(request.placements) or 'Insufficient data'}\n"
                    f"Objective: {request.objective_label} — {request.objective_description}\n"
                    f"Platform optimization goal: {request.optimization}\n"
                    f"Metrics this objective is normally judged by: "
                    f"{', '.join(request.suggested_success_metrics) or 'not specified'}\n"
                    f"Offer: {request.offer or 'Not supplied — infer from products/services or state Insufficient data.'}\n"
                    f"Audience brief: {request.audience or context.target_audience or 'Not supplied'}\n"
                    f"Requested tone: {request.tone or context.brand_voice or 'Not supplied'}\n"
                    f"Total budget: {request.total_budget} {request.currency}\n"
                    f"Daily budget: {request.daily_budget} {request.currency}\n"
                    f"Monthly budget: {request.monthly_budget} {request.currency}\n"
                    f"Duration: {request.duration_days} days\n\n"
                    "Historical evidence from stored records (may be empty — if it is, "
                    "say so rather than assuming performance):\n"
                    f"{request.historical_evidence}\n\n"
                    "Data gaps already identified by the platform, which you must "
                    "carry into `data_limitations`:\n"
                    f"{request.known_data_gaps}\n\n"
                    f"Client context JSON:\n{context.model_dump_json()}"
                ),
            ),
        ]
