from pydantic import BaseModel, Field

from app.ai.agents.base import BaseAgent
from app.ai.providers.base import Message
from app.schemas.client import ClientContext


class VideoAgentRequest(BaseModel):
    objective: str = "Lead generation"
    platform: str = "instagram"
    format: str = "Reel"
    offer: str | None = None
    count: int = 3


class VideoConcept(BaseModel):
    title: str
    hook: str
    script: str
    visual_notes: str
    cta: str
    duration_seconds: int = 15


class VideoPack(BaseModel):
    concepts: list[VideoConcept] = Field(default_factory=list)
    insufficient_data: list[str] = Field(default_factory=list)


class VideoAgent(BaseAgent[VideoAgentRequest, VideoPack]):
    name = "VideoAgent"
    output_schema = VideoPack

    def build_messages(self, context: ClientContext, request: VideoAgentRequest) -> list[Message]:
        return [
            Message(
                role="system",
                content=(
                    "You are VideoAgent for GrowthOS. Produce short-form video concepts and scripts only. "
                    "Never claim a video file was generated or published."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Objective: {request.objective}\nPlatform: {request.platform}\nFormat: {request.format}\n"
                    f"Offer: {request.offer}\nCount: {request.count}\n"
                    f"Client: {context.model_dump_json()}"
                ),
            ),
        ]
