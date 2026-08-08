from app.ai.agents.base import BaseAgent
from app.ai.providers.base import Message
from app.schemas.client import ClientContext
from app.schemas.content import ContentGenerateRequest, ContentGenerated


class ContentAgent(BaseAgent[ContentGenerateRequest, ContentGenerated]):
    name = "ContentAgent"
    output_schema = ContentGenerated

    def build_messages(self, context: ClientContext, request: ContentGenerateRequest) -> list[Message]:
        return [
            Message(
                role="system",
                content=(
                    "You are ContentAgent for GrowthOS AI. Match brand voice. "
                    "Do not invent performance claims. Output hook, copy, CTA, visual/video concepts, hashtags."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Generate {request.content_type} for {request.platform}.\n"
                    f"Objective: {request.objective}\n"
                    f"Audience: {request.audience or context.target_audience or 'Insufficient data.'}\n"
                    f"Tone: {request.tone or context.brand_voice or 'professional'}\n"
                    f"Topic: {request.topic}\n"
                    f"CTA: {request.cta or 'Learn more'}\n"
                    f"Client: {context.business_name} | {context.industry}\n"
                    f"Brand voice: {context.brand_voice or 'Insufficient data.'}"
                ),
            ),
        ]
