"""
VariationAgent — one deliberate change per variation, so a test can be read.

A variation is only useful if you can say what changed and what you expect to
learn. Change the hook and the visual and the CTA at once and a winner tells you
nothing about which change won.

So each variation declares a single `axis` and a `hypothesis`. The agent is asked
to hold everything else steady, and the enum on `axis` means a variation cannot be
recorded without naming what it altered.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.ai.agents.base import BaseAgent
from app.ai.agents.campaign_strategy_agent import GROUNDING_RULES
from app.ai.providers.base import Message
from app.schemas.campaign_generation import VariationPack
from app.schemas.client import ClientContext

#: What changing each axis is for. Given to the agent so a variation is chosen
#: because it answers a question, not because the axis was next in a list.
AXIS_GUIDANCE = {
    "hook": "Replace the opening line with one built on a different tension. Tests what stops this audience scrolling.",
    "visual": "Change the subject or setting of the image. Tests which imagery earns attention.",
    "offer": "Reframe how the same offer is presented — emphasis, framing, packaging. Never invent a different price or a discount that was not supplied.",
    "cta": "Change the ask, including how much commitment it implies. Tests conversion friction.",
    "tone": "Shift the register while keeping the argument. Tests how this audience prefers to be spoken to.",
    "composition": "Keep the subject, change framing, crop or layout. Tests visual hierarchy.",
    "format": "Change the aspect ratio or creative type for a different placement.",
    "audience_angle": "Speak to a different segment or a different moment in the buying process.",
}


class VariationRequest(BaseModel):
    count: int = 3
    platform: str = "meta"
    platform_label: str = "Meta (Facebook)"
    objective: str = "lead_generation"
    #: Restrict which axes may be used. Empty lets the agent choose the axes most
    #: worth testing for this concept.
    allowed_axes: list[str] = Field(default_factory=list)
    allowed_aspect_ratios: list[str] = Field(default_factory=list)
    needs_media: bool = False
    #: The concept being varied, including its copy and visual direction.
    parent_concept: dict = Field(default_factory=dict)
    brief: dict = Field(default_factory=dict)
    #: References already taken by the parent and its existing variations, so a
    #: new one does not collide.
    used_references: list[str] = Field(default_factory=list)
    known_data_gaps: list[str] = Field(default_factory=list)


class VariationAgent(BaseAgent[VariationRequest, VariationPack]):
    name = "VariationAgent"
    output_schema = VariationPack

    def build_messages(self, context: ClientContext, request: VariationRequest) -> list[Message]:
        axes = request.allowed_axes or list(AXIS_GUIDANCE)
        axis_lines = "\n".join(
            f"  - {axis}: {AXIS_GUIDANCE[axis]}" for axis in axes if axis in AXIS_GUIDANCE
        )
        media_instruction = (
            "Each variation whose axis is visual, composition or format must include an "
            "`image_prompt` (and `video_prompt` when `creative_type` is video) written "
            "to the same specificity as the parent: subject, environment, lighting, "
            "framing. Set `creative_type` to image or video accordingly."
            if request.needs_media
            else "Set `creative_type` to 'copy' unless the axis is inherently visual. "
            "Prompts are optional; no media will be generated from this request."
        )
        return [
            Message(
                role="system",
                content=(
                    "You are VariationAgent for GrowthOS. You produce testable "
                    "variations of an existing creative concept.\n\n"
                    f"{GROUNDING_RULES}\n\n"
                    "ONE CHANGE PER VARIATION — the rule that makes a test readable:\n"
                    "Each variation changes exactly one axis and holds the rest of the "
                    "parent concept steady. Copy unchanged fields through from the "
                    "parent rather than rewriting them.\n\n"
                    "Axes you may use:\n" + axis_lines + "\n\n"
                    "`hypothesis` states what this variation tests and what result "
                    "would confirm it — for example 'If loss-framing outperforms "
                    "aspiration on the hook, this audience is further from purchase "
                    "than assumed.'\n\n"
                    "A variation that only swaps synonyms is a failure. 'Boost your "
                    "revenue' → 'Increase your revenue' changes nothing that can be "
                    "learned. Change the underlying idea on the chosen axis.\n\n"
                    f"{media_instruction}"
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Produce {request.count} variations of the concept below.\n\n"
                    f"Parent concept:\n{request.parent_concept}\n\n"
                    "Set `parent_concept_id` on every variation to "
                    f"{request.parent_concept.get('concept_id') or request.parent_concept.get('reference') or 'A'}.\n"
                    f"References already in use, which you must not reuse: "
                    f"{request.used_references or 'none'}\n"
                    "Assign each variation a distinct single-letter `reference`.\n\n"
                    f"Platform: {request.platform_label}\n"
                    f"Objective: {request.objective}\n"
                    f"Aspect ratios permitted on this platform: "
                    f"{request.allowed_aspect_ratios or 'use the parent ratio'}\n\n"
                    f"Creative brief:\n{request.brief}\n\n"
                    f"Data gaps to carry into `data_limitations`:\n{request.known_data_gaps}\n\n"
                    f"Client context JSON:\n{context.model_dump_json()}"
                ),
            ),
        ]
