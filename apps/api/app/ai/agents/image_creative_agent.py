from pydantic import BaseModel, Field

from app.ai.agents.base import BaseAgent
from app.ai.providers.base import Message
from app.schemas.client import ClientContext


class ImageCreativeRequest(BaseModel):
    objective: str = "Lead generation"
    platform: str = "instagram"
    offer: str | None = None
    count: int = 5
    styles: list[str] = Field(
        default_factory=lambda: [
            "hero",
            "product",
            "problem_solution",
            "offer",
            "ugc",
        ]
    )


class ImagePromptItem(BaseModel):
    style: str
    prompt: str
    headline_suggestion: str | None = None
    notes: str | None = None


class ImageCreativePack(BaseModel):
    prompts: list[ImagePromptItem] = Field(default_factory=list)
    insufficient_data: list[str] = Field(default_factory=list)


class ImageCreativeAgent(BaseAgent[ImageCreativeRequest, ImageCreativePack]):
    name = "ImageCreativeAgent"
    output_schema = ImageCreativePack

    def build_messages(self, context: ClientContext, request: ImageCreativeRequest) -> list[Message]:
        return [
            Message(
                role="system",
                content=(
                    "You are ImageCreativeAgent. Produce image generation prompts only — do not claim images exist. "
                    "Align with brand voice and offer. No fabricated performance claims."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Objective: {request.objective}\nPlatform: {request.platform}\nOffer: {request.offer}\n"
                    f"Count: {request.count}\nStyles: {request.styles}\n"
                    f"Client: {context.model_dump_json()}"
                ),
            ),
        ]
