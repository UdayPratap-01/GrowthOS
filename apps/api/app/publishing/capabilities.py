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
        msg = "Configure GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_ADS_DEVELOPER_TOKEN."
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
