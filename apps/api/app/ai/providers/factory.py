from app.ai.providers.anthropic_provider import AnthropicProvider
from app.ai.providers.base import AIProvider
from app.ai.providers.mock import MockAIProvider
from app.ai.providers.openai_provider import OpenAIProvider
from app.core.config import get_settings


def get_ai_provider() -> AIProvider:
    settings = get_settings()
    provider = settings.ai_provider.lower()
    if provider == "openai":
        return OpenAIProvider()
    if provider == "anthropic":
        return AnthropicProvider()
    return MockAIProvider()
