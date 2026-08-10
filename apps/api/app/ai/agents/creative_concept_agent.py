"""
CreativeConceptAgent — the visual half of each concept, and the provider prompts.

This agent produces what actually gets sent to an image or video model, so a weak
prompt here becomes a stock-photo-looking asset that tells the reviewer nothing
about whether the angle works. "Create a beautiful marketing image" is the
specific failure being designed against.

The prompt therefore demands a described photograph rather than a wish: subject,
environment, lighting, composition and style, tied to this client's actual product
and audience. `negative_constraints` carries what must not appear — the thing
image models most reliably get wrong (garbled text, fake logos, invented awards).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.ai.agents.base import BaseAgent
from app.ai.agents.campaign_strategy_agent import GROUNDING_RULES
from app.ai.providers.base import Message
from app.schemas.campaign_generation import CreativeConceptPack
from app.schemas.client import ClientContext

#: Applied to every generated asset regardless of what the agent returns. These
#: are the failures that make an asset unusable rather than merely off-brief, so
#: they are enforced in code, not left to the model to remember.
BASELINE_NEGATIVE_CONSTRAINTS = (
    "no garbled or misspelled text",
    "no invented brand logos or trademarks",
    "no fabricated awards, ratings, review scores or statistics",
    "no stock-photo handshakes or generic office backdrops",
    "no watermarks or provider signatures",
    "no distorted hands, faces or limbs",
)


class CreativeConceptRequest(BaseModel):
    platform: str = "meta"
    platform_label: str = "Meta (Facebook)"
    objective: str = "lead_generation"
    aspect_ratios: list[str] = Field(default_factory=lambda: ["1:1"])
    #: Resolved shape guidance so the agent composes for the format instead of
    #: describing a square idea that gets cropped into a vertical placement.
    aspect_ratio_guidance: list[dict] = Field(default_factory=list)
    needs_video: bool = False
    brief: dict = Field(default_factory=dict)
    strategy: dict = Field(default_factory=dict)
    #: The copy concepts these visuals have to serve, each with its concept_id.
    copy_concepts: list[dict] = Field(default_factory=list)
    known_data_gaps: list[str] = Field(default_factory=list)


class CreativeConceptAgent(BaseAgent[CreativeConceptRequest, CreativeConceptPack]):
    name = "CreativeConceptAgent"
    output_schema = CreativeConceptPack

    def build_messages(
        self, context: ClientContext, request: CreativeConceptRequest
    ) -> list[Message]:
        video_instruction = (
            "Also write `video_prompt`: a single continuous shot description for a "
            "short-form video model. Describe camera movement, pacing and what "
            "happens across the clip. Video models do not render reliable text, so "
            "carry the message in the imagery, not in overlays."
            if request.needs_video
            else "Leave `video_prompt` null — no video was requested."
        )
        return [
            Message(
                role="system",
                content=(
                    "You are CreativeConceptAgent for GrowthOS. For each copy concept "
                    "you specify the visual that will carry it, and you write the "
                    "prompts that go to the image and video generation models.\n\n"
                    f"{GROUNDING_RULES}\n\n"
                    "PROMPT QUALITY — the requirement this agent exists for:\n"
                    "`image_prompt` must read like a description of a photograph that "
                    "already exists. Name the subject, what it is doing, where it is, "
                    "how it is lit, the lens or framing, and the colour treatment. "
                    "Ground it in this client's actual product or service and this "
                    "audience.\n"
                    "Rejected as too generic: 'a beautiful marketing image', 'a "
                    "professional business photo', 'an eye-catching advertisement'. "
                    "If your prompt would produce the same picture for a dental clinic "
                    "and a SaaS company, it is wrong.\n"
                    "Leave deliberate negative space where a headline will sit, and say "
                    "in `text_overlay` what goes there — but do not ask the image model "
                    "to render the words, because it cannot do so reliably.\n\n"
                    f"{video_instruction}\n\n"
                    "`negative_constraints` lists what must not appear. Add anything "
                    "specific to this client or industry, such as claims that would be "
                    "regulated."
                ),
            ),
            Message(
                role="user",
                content=(
                    "Produce one visual specification per copy concept.\n\n"
                    "Set `concept_id` to match the copy concept it serves. Return one "
                    "spec for every concept below, no more and no fewer.\n\n"
                    f"Copy concepts:\n{request.copy_concepts}\n\n"
                    f"Platform: {request.platform_label}\n"
                    f"Aspect ratios to design for: {request.aspect_ratios}\n"
                    f"Shape guidance: {request.aspect_ratio_guidance}\n"
                    "Set `aspect_ratios` on each spec to the ratios above.\n\n"
                    f"Creative brief:\n{request.brief}\n\n"
                    f"Creative strategy from the campaign strategy:\n"
                    f"{request.strategy.get('creative_strategy') if isinstance(request.strategy, dict) else ''}\n\n"
                    f"Data gaps to carry into `data_limitations`:\n{request.known_data_gaps}\n\n"
                    f"Client context JSON:\n{context.model_dump_json()}"
                ),
            ),
        ]
