from __future__ import annotations

from app.integrations.base import MarketingIntegration
from app.integrations.google_ads import GoogleAdsIntegration
from app.integrations.google_analytics import GoogleAnalyticsIntegration
from app.integrations.meta_family import InstagramIntegration, MetaIntegration, WhatsAppIntegration
from app.integrations.youtube import YouTubeIntegration

PHASE3_PROVIDERS = ("meta", "instagram", "whatsapp", "google_analytics")
PHASE4_PROVIDERS = ("google_ads", "youtube")


def build_integrations() -> dict[str, MarketingIntegration]:
    return {
        "meta": MetaIntegration(),
        "instagram": InstagramIntegration(),
        "whatsapp": WhatsAppIntegration(),
        "google_analytics": GoogleAnalyticsIntegration(),
        "google_ads": GoogleAdsIntegration(),
        "youtube": YouTubeIntegration(),
    }


def list_integrations() -> list[MarketingIntegration]:
    return list(build_integrations().values())


def get_integration(provider: str) -> MarketingIntegration | None:
    return build_integrations().get(provider)
