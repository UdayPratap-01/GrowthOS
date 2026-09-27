"""Provider capability definitions for publishing and ads execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CapabilityStatus(str, Enum):
    supported = "SUPPORTED"
    unsupported = "UNSUPPORTED"
    not_configured = "NOT_CONFIGURED"
    not_connected = "NOT_CONNECTED"


@dataclass(frozen=True)
class ProviderCapability:
    operation: str
    status: CapabilityStatus
    message: str = ""


@dataclass
class ProviderCapabilityMatrix:
    provider: str
    capabilities: list[ProviderCapability] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "capabilities": [
                {"operation": c.operation, "status": c.status.value, "message": c.message} for c in self.capabilities
            ],
        }


# Operations the execution layer understands.
ADS_OPERATIONS = (
    "create_campaign",
    "create_ad_set",
    "create_ad",
    "pause",
    "resume",
    "update_budget",
    "get_status",
    "get_metrics",
)

SOCIAL_OPERATIONS = (
    "publish_post",
    "schedule_post",
    "get_status",
    "delete",
)


def meta_ads_capabilities(*, connected: bool, credentials_configured: bool) -> ProviderCapabilityMatrix:
    caps: list[ProviderCapability] = []
    if not credentials_configured:
        base = CapabilityStatus.not_configured
        msg = "Configure META_APP_ID and META_APP_SECRET."
    elif not connected:
        base = CapabilityStatus.not_connected
        msg = "Connect Meta via OAuth."
    else:
        base = None
        msg = ""

    for op in ADS_OPERATIONS:
        if base is not None:
            caps.append(ProviderCapability(op, base, msg))
            continue
        if op in {"pause", "resume", "update_budget", "get_status", "get_metrics"}:
            caps.append(
                ProviderCapability(
                    op,
                    CapabilityStatus.supported,
                    "Requires campaign external_id from prior sync or publish.",
                )
            )
        else:
            caps.append(
                ProviderCapability(
                    op,
                    CapabilityStatus.unsupported,
                    "Full campaign/ad creation requires Marketing API write adapter (not enabled).",
                )
            )
    return ProviderCapabilityMatrix(provider="meta", capabilities=caps)


def google_ads_capabilities(*, connected: bool, credentials_configured: bool) -> ProviderCapabilityMatrix:
    caps: list[ProviderCapability] = []
    if not credentials_configured:
        base = CapabilityStatus.not_configured
        msg = "Configure GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
    elif not connected:
        base = CapabilityStatus.not_connected
        msg = "Connect Google Ads via OAuth."
    else:
        base = None
        msg = ""

    for op in ADS_OPERATIONS:
        if base is not None:
            caps.append(ProviderCapability(op, base, msg))
            continue
        if op in {"pause", "resume", "get_metrics"}:
            caps.append(
                ProviderCapability(
                    op,
                    CapabilityStatus.supported,
                    "Requires synced campaign resource id in campaign.metrics.external_campaign_id.",
                )
            )
        elif op == "update_budget":
            caps.append(
                ProviderCapability(
                    op,
                    CapabilityStatus.unsupported,
                    "Google Ads budget mutate requires additional adapter configuration.",
                )
            )
        else:
            caps.append(
                ProviderCapability(
                    op,
                    CapabilityStatus.unsupported,
                    "Campaign creation via Mutate API is not enabled in this release.",
                )
            )
    return ProviderCapabilityMatrix(provider="google_ads", capabilities=caps)


YOUTUBE_READ_OPERATIONS = (
    "get_channel",
    "list_videos",
    "get_video",
    "get_channel_analytics",
)

WHATSAPP_READ_OPERATIONS = (
    "get_business_account",
    "list_phone_numbers",
    "get_connection_status",
    "receive_webhook",
    "send_message",
)

GSC_READ_OPERATIONS = (
    "list_sites",
    "search_analytics",
    "url_inspection",
)


def youtube_capabilities(*, connected: bool, credentials_configured: bool) -> ProviderCapabilityMatrix:
    caps: list[ProviderCapability] = []
    if not credentials_configured:
        base = CapabilityStatus.not_configured
        msg = "Configure GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
    elif not connected:
        base = CapabilityStatus.not_connected
        msg = "Connect YouTube via Google OAuth."
    else:
        base = None
        msg = ""

    for op in YOUTUBE_READ_OPERATIONS:
        if base is not None:
            caps.append(ProviderCapability(op, base, msg))
        else:
            caps.append(
                ProviderCapability(
                    op,
                    CapabilityStatus.supported,
                    "Read-only via YouTube Data API v3.",
                )
            )
    for op in ("publish_video", "delete_video", "update_video"):
        caps.append(
            ProviderCapability(
                op,
                CapabilityStatus.unsupported,
                "Video publishing is not enabled in this release.",
            )
        )
    return ProviderCapabilityMatrix(provider="youtube", capabilities=caps)


def whatsapp_capabilities(*, connected: bool, credentials_configured: bool) -> ProviderCapabilityMatrix:
    caps: list[ProviderCapability] = []
    if not credentials_configured:
        base = CapabilityStatus.not_configured
        msg = "Configure META_APP_ID and META_APP_SECRET."
    elif not connected:
        base = CapabilityStatus.not_connected
        msg = "Connect WhatsApp via Meta OAuth."
    else:
        base = None
        msg = ""

    for op in WHATSAPP_READ_OPERATIONS[:3]:
        if base is not None:
            caps.append(ProviderCapability(op, base, msg))
        else:
            caps.append(
                ProviderCapability(
                    op,
                    CapabilityStatus.supported,
                    "Read-only WABA metadata via Meta Graph API.",
                )
            )
    caps.append(
        ProviderCapability(
            "receive_webhook",
            CapabilityStatus.unsupported if base is None else base,
            "Requires public HTTPS webhook URL (pending production domain)."
            if base is None
            else msg,
        )
    )
    caps.append(
        ProviderCapability(
            "send_message",
            CapabilityStatus.unsupported,
            "Outbound messaging disabled — no autonomous messaging.",
        )
    )
    return ProviderCapabilityMatrix(provider="whatsapp", capabilities=caps)


def google_search_console_capabilities(
    *, connected: bool, credentials_configured: bool
) -> ProviderCapabilityMatrix:
    caps: list[ProviderCapability] = []
    if not credentials_configured:
        base = CapabilityStatus.not_configured
        msg = "Configure GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
    elif not connected:
        base = CapabilityStatus.not_connected
        msg = "Connect Search Console via Google OAuth."
    else:
        base = None
        msg = ""

    for op in GSC_READ_OPERATIONS:
        if base is not None:
            caps.append(ProviderCapability(op, base, msg))
        elif op == "url_inspection":
            caps.append(
                ProviderCapability(
                    op,
                    CapabilityStatus.unsupported,
                    "URL Inspection API requires additional adapter wiring.",
                )
            )
        else:
            caps.append(
                ProviderCapability(
                    op,
                    CapabilityStatus.supported,
                    "Read-only via Search Console API.",
                )
            )
    return ProviderCapabilityMatrix(provider="google_search_console", capabilities=caps)


def instagram_publish_capabilities(*, connected: bool) -> ProviderCapabilityMatrix:
    if not connected:
        return ProviderCapabilityMatrix(
            provider="instagram",
            capabilities=[
                ProviderCapability("publish_post", CapabilityStatus.not_connected, "Connect Instagram via Meta OAuth."),
                ProviderCapability("schedule_post", CapabilityStatus.not_connected, "Connect Instagram via Meta OAuth."),
            ],
        )
    return ProviderCapabilityMatrix(
        provider="instagram",
        capabilities=[
            ProviderCapability(
                "publish_post",
                CapabilityStatus.unsupported,
                "Organic Instagram publishing requires instagram_content_publish scope (not configured).",
            ),
            ProviderCapability(
                "schedule_post",
                CapabilityStatus.unsupported,
                "Instagram scheduling requires content publishing API (not configured).",
            ),
        ],
    )
