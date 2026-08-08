from pydantic import BaseModel, Field

from app.ai.agents.base import BaseAgent
from app.ai.providers.base import Message
from app.schemas.client import ClientContext


class CreativeRequest(BaseModel):
    platform: str = "instagram"
    format: str = "Reel"
    objective: str = "Lead generation"
    topic: str | None = None
    count: int = 3


class CreativeConcept(BaseModel):
    headline: str
    primary_text: str
    cta: str
    visual_concept: str
    variation_notes: str | None = None


class CreativePack(BaseModel):
    concepts: list[CreativeConcept] = Field(default_factory=list)
    brand_alignment_notes: str
    insufficient_data: list[str] = Field(default_factory=list)


class CreativeAgent(BaseAgent[CreativeRequest, CreativePack]):
    name = "CreativeAgent"
    output_schema = CreativePack

    def build_messages(self, context: ClientContext, request: CreativeRequest) -> list[Message]:
        return [
            Message(
                role="system",
                content=(
                    "You are CreativeAgent for GrowthOS. Use client brand voice, products, audience, and offers. "
                    "Do not invent performance metrics. If brand details are thin, list Insufficient data."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Platform: {request.platform}\nFormat: {request.format}\nObjective: {request.objective}\n"
                    f"Topic: {request.topic or 'Offer-focused'}\nCount: {request.count}\n"
                    f"Client: {context.model_dump_json()}"
                ),
            ),
        ]
