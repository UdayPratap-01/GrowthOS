"""
CopyAgent — several genuinely different marketing hypotheses, not several wordings.

The requirement this agent exists to satisfy is that three concepts must be three
different bets. The usual failure is subtler than duplication: a model asked for
"3 variations" returns one idea rephrased three times, all three test the same
hypothesis, and the resulting A/B/C test cannot distinguish between them.

Two mechanisms push against that. The prompt names concrete, mutually exclusive
angle families and requires a different one per concept. And every concept must
carry a `hypothesis` stating what it is testing — a claim that becomes obviously
duplicated when two concepts share it, which makes the failure visible to a
reviewer instead of invisible.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.ai.agents.base import BaseAgent
from app.ai.agents.campaign_strategy_agent import GROUNDING_RULES
from app.ai.providers.base import Message
from app.schemas.campaign_generation import CopyConceptPack
from app.schemas.client import ClientContext

#: Angle families, described by the customer psychology each one bets on. Given
#: as vocabulary, not as a fixed list to walk: the agent picks whichever suit the
#: offer, and must pick a different family per concept.
ANGLE_FAMILIES = (
    "problem-agitate — name the cost of the status quo, then relieve it",
    "aspiration — show the outcome the customer wants to be true of them",
    "objection-handling — lead with the reason they have not bought yet",
    "social-proof — lead with evidence others made this choice (only if the "
    "context supports it; never invent a testimonial or a customer count)",
    "authority — lead with the expertise or method behind the offer",
    "direct-offer — lead with the offer itself, for audiences already in market",
    "curiosity-gap — open a question the ad answers, without clickbait",
    "contrarian — challenge a belief the audience holds about the category",
)


class CopyRequest(BaseModel):
    count: int = 3
    platform: str = "meta"
    platform_label: str = "Meta (Facebook)"
    objective: str = "lead_generation"
    objective_label: str = "Lead Generation"
    headline_max_chars: int = 40
    primary_text_max_chars: int = 125
    description_max_chars: int = 30
    tone: str | None = None
    cta: str | None = None
    brief: dict = Field(default_factory=dict)
    strategy: dict = Field(default_factory=dict)
    known_data_gaps: list[str] = Field(default_factory=list)


class CopyAgent(BaseAgent[CopyRequest, CopyConceptPack]):
    name = "CopyAgent"
    output_schema = CopyConceptPack

    def build_messages(self, context: ClientContext, request: CopyRequest) -> list[Message]:
        references = ", ".join(_references(request.count))
        return [
            Message(
                role="system",
                content=(
                    "You are CopyAgent for GrowthOS. You write performance ad copy for "
                    "a specific client, working from an approved creative brief.\n\n"
                    f"{GROUNDING_RULES}\n\n"
                    "DISTINCTNESS — the requirement that matters most here:\n"
                    "Each concept must test a different marketing hypothesis, drawn "
                    "from a different angle family. Angle families available:\n"
                    + "\n".join(f"  - {family}" for family in ANGLE_FAMILIES)
                    + "\n\n"
                    "Two concepts that could be swapped without changing what is being "
                    "learned are a failure, however different the wording. State what "
                    "each concept tests in `hypothesis`. If two hypotheses read the "
                    "same, rewrite one from a different family.\n\n"
                    "CRAFT:\n"
                    "- `hook` is the first line, written to stop a scroll. Specific, "
                    "concrete, no throat-clearing.\n"
                    "- `primary_text` carries the argument: tension, then the offer as "
                    "resolution, then a reason to act now that is true.\n"
                    "- `headline` is short and complements the hook rather than "
                    "repeating it.\n"
                    "- `cta` matches the objective and the friction of the ask.\n"
                    "- Write in the client's brand voice. No exclamation stacking, no "
                    "'unlock', 'supercharge', 'game-changer', 'in today's world'.\n"
                    "- Never state a statistic, guarantee or customer count that is "
                    "not in the context."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Write {request.count} distinct ad concepts.\n\n"
                    f"Use exactly these values for `concept_id`, in order: {references}\n\n"
                    f"Platform: {request.platform_label}\n"
                    f"Objective: {request.objective_label} — set this as `objective` on "
                    "every concept.\n"
                    f"Length guidance for {request.platform_label}: headline ≤ "
                    f"{request.headline_max_chars} characters, primary text ≤ "
                    f"{request.primary_text_max_chars} characters, description ≤ "
                    f"{request.description_max_chars} characters. Stay close to these; "
                    "do not pad to reach them.\n"
                    f"Tone: {request.tone or context.brand_voice or 'Use the brand voice in the context'}\n"
                    f"Preferred CTA: {request.cta or 'Choose per concept'}\n\n"
                    f"Creative brief:\n{request.brief}\n\n"
                    f"Campaign strategy:\n{request.strategy}\n\n"
                    f"Data gaps to carry into `data_limitations`:\n{request.known_data_gaps}\n\n"
                    f"Client context JSON:\n{context.model_dump_json()}"
                ),
            ),
        ]


def _references(count: int) -> list[str]:
    """A, B, C … so a concept is referable in conversation and in the UI."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return [alphabet[i % len(alphabet)] for i in range(max(1, count))]
