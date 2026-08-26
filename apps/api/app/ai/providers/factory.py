"""
AI provider selection.

There is deliberately no fallback: an unknown or misconfigured provider raises
instead of quietly returning the mock, which would fabricate marketing analysis
and present it as real.
"""

from app.ai.providers.anthropic_provider import AnthropicProvider
from app.ai.providers.base import AIProvider
from app.ai.providers.mock import MockAIProvider
from app.ai.providers.openai_provider import OpenAIProvider
from app.core.config import get_settings

KNOWN_PROVIDERS = ("openai", "anthropic", "mock")
SIMULATED_PROVIDERS = frozenset({"mock", "fake", "stub", "demo"})


class AIProviderConfigurationError(RuntimeError):
    """Raised when AI_PROVIDER is unknown, unusable, or unsafe for this environment."""


def get_ai_provider() -> AIProvider:
    """Returns the configured provider wrapped so every call is metered."""
    from app.ai.providers.metering import MeteredProvider

    return MeteredProvider(_select_provider())


def _select_provider() -> AIProvider:
    settings = get_settings()
    provider = (settings.ai_provider or "").strip().lower()

    if provider in SIMULATED_PROVIDERS:
        if settings.is_production:
            raise AIProviderConfigurationError(
                f"AI_PROVIDER={provider!r} is not permitted in production. "
                "The mock provider returns fabricated analysis and metrics. "
                "Configure AI_PROVIDER=openai or AI_PROVIDER=anthropic."
            )
        if provider != "mock":
            raise AIProviderConfigurationError(
                f"Unknown AI_PROVIDER {settings.ai_provider!r}. Expected one of: {', '.join(KNOWN_PROVIDERS)}."
            )
        return MockAIProvider()

    if provider == "openai":
        if not (settings.openai_api_key or "").strip():
            raise AIProviderConfigurationError(
                "AI_PROVIDER=openai but OPENAI_API_KEY is not set. "
                "Set a real key or choose a different provider."
            )
        return OpenAIProvider()

    if provider == "anthropic":
        if not (settings.anthropic_api_key or "").strip():
            raise AIProviderConfigurationError(
                "AI_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set. "
                "Set a real key or choose a different provider."
            )
        return AnthropicProvider()

    raise AIProviderConfigurationError(
        f"Unknown AI_PROVIDER {settings.ai_provider!r}. Expected one of: {', '.join(KNOWN_PROVIDERS)}."
    )
