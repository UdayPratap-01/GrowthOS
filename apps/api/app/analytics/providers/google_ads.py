"""Google Ads search metrics → NormalizedPerformanceRow (read-only)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.errors import (
    CredentialsExpired,
    CredentialsMissing,
    IntegrationDisconnected,
    MalformedProviderResponse,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderTransportError,
)
from app.analytics.normalize import NormalizedPerformanceRow
from app.core.config import get_settings
from app.integrations.google_oauth import ensure_access_token
from app.integrations.persistence import get_integration_row

ADS_API = "https://googleads.googleapis.com/v18"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_decimal(value: Any, default: str = "0") -> Decimal:
    try:
        if value is None:
            return Decimal(default)
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def normalize_google_ads_row(
    *,
    organization_id: UUID,
    client_id: UUID | None,
    customer_id: str,
    item: dict[str, Any],
) -> NormalizedPerformanceRow | None:
    if not isinstance(item, dict):
        return None
    campaign = item.get("campaign") or {}
    metrics = item.get("metrics") or {}
    segments = item.get("segments") or {}
    if not isinstance(campaign, dict) or not isinstance(metrics, dict) or not isinstance(segments, dict):
        return None

    ext_id = str(campaign.get("id") or "")
    day_raw = segments.get("date")
    if not ext_id or not day_raw:
        return None
    try:
        day = date.fromisoformat(str(day_raw)[:10])
    except ValueError:
        return None

    cost_micros = _safe_decimal(metrics.get("costMicros") or metrics.get("cost_micros"))
    spend = (cost_micros / Decimal("1000000")).quantize(Decimal("0.0001"))
    conversions = _safe_decimal(metrics.get("conversions"))
    conv_value = _safe_decimal(
        metrics.get("conversionsValue") or metrics.get("conversions_value")
    )

    return NormalizedPerformanceRow(
        organization_id=organization_id,
        client_id=client_id,
        platform="google_ads",
        entity_level="campaign",
        date=day,
        external_account_id=str(customer_id or ""),
        external_campaign_id=ext_id,
        impressions=_safe_int(metrics.get("impressions")),
        reach=None,
        clicks=_safe_int(metrics.get("clicks")),
        spend=spend,
        conversions=conversions,
        leads=int(conversions) if conversions == conversions.to_integral_value() else int(conversions),
        revenue=conv_value,
        currency="USD",
        provider_metadata={
            "source": "google_ads_search",
            "campaign_name": campaign.get("name"),
            "campaign_status": campaign.get("status"),
            "customer_id": customer_id,
        },
    )


class GoogleAdsInsightsFetcher:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def fetch(
        self,
        *,
        organization_id: UUID,
        client_id: UUID | None,
        lookback_days: int,
        entity_level: str = "campaign",
    ) -> list[NormalizedPerformanceRow]:
        if entity_level not in {"campaign", "account"}:
            # Ad/ad_set grain not implemented for Google in this milestone.
            from app.analytics.errors import UnsupportedOperation

            raise UnsupportedOperation(
                f"Google Ads ingestion does not support entity_level={entity_level!r}"
            )

        row = await get_integration_row(
            self.db, organization_id=organization_id, provider="google_ads", client_id=client_id
        )
        if not row or not row.secret_ref:
            row = await get_integration_row(
                self.db, organization_id=organization_id, provider="google_ads", client_id=None
            )
        if not row:
            raise IntegrationDisconnected("Google Ads integration is not connected")
        if row.status != "connected" or not row.secret_ref:
            raise IntegrationDisconnected("Google Ads integration is not connected")

        settings = get_settings()
        if not settings.google_ads_developer_token:
            raise CredentialsMissing("GOOGLE_ADS_DEVELOPER_TOKEN is not configured")

        try:
            access_token = await ensure_access_token(
                self.db,
                row,
                organization_id=organization_id,
                provider="google_ads",
                client_id=client_id,
            )
        except Exception as exc:
            message = str(exc).lower()
            if "expired" in message or "invalid_grant" in message:
                raise CredentialsExpired("Google Ads credentials expired") from exc
            raise CredentialsMissing(f"Google Ads token unavailable: {str(exc)[:200]}") from exc

        if not access_token:
            raise CredentialsMissing("Google Ads access token is missing")

        customer_id = (row.config or {}).get("customer_id") or (
            row.config or {}
        ).get("external_account_id")
        if not customer_id:
            customer_id = await self._discover_customer(access_token)
        customer_clean = str(customer_id).replace("-", "")

        end = date.today()
        start = end - timedelta(days=max(1, lookback_days) - 1)
        query = f"""
            SELECT
              campaign.id,
              campaign.name,
              campaign.status,
              segments.date,
              metrics.impressions,
              metrics.clicks,
              metrics.cost_micros,
              metrics.conversions,
              metrics.conversions_value
            FROM campaign
            WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'
        """

        headers = {
            "Authorization": f"Bearer {access_token}",
            "developer-token": settings.google_ads_developer_token or "",
            "Content-Type": "application/json",
        }
        if settings.google_ads_login_customer_id:
            headers["login-customer-id"] = settings.google_ads_login_customer_id.replace("-", "")

        url = f"{ADS_API}/customers/{customer_clean}/googleAds:search"
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, headers=headers, json={"query": query})
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("Google Ads search timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransportError(f"Google Ads transport error: {str(exc)[:200]}") from exc

        if resp.status_code in {401, 403}:
            raise CredentialsExpired("Google Ads credentials rejected")
        if resp.status_code == 429:
            raise ProviderRateLimited("Google Ads rate limited")
        if resp.status_code >= 400:
            raise ProviderTransportError(f"Google Ads search HTTP {resp.status_code}")

        try:
            body = resp.json()
        except ValueError as exc:
            raise MalformedProviderResponse("Google Ads response was not JSON") from exc
        if not isinstance(body, dict):
            raise MalformedProviderResponse("Google Ads body was not an object")

        results = body.get("results") or []
        if not isinstance(results, list):
            raise MalformedProviderResponse("Google Ads results was not a list")

        rows: list[NormalizedPerformanceRow] = []
        for item in results:
            normalized = normalize_google_ads_row(
                organization_id=organization_id,
                client_id=client_id,
                customer_id=customer_clean,
                item=item if isinstance(item, dict) else {},
            )
            if normalized:
                if entity_level == "account":
                    # Collapse to account grain for callers that request it.
                    normalized.entity_level = "account"
                    normalized.external_campaign_id = ""
                rows.append(normalized)
        return rows

    async def _discover_customer(self, access_token: str) -> str:
        settings = get_settings()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "developer-token": settings.google_ads_developer_token or "",
            "Content-Type": "application/json",
        }
        if settings.google_ads_login_customer_id:
            headers["login-customer-id"] = settings.google_ads_login_customer_id.replace("-", "")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{ADS_API}/customers:listAccessibleCustomers", headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("Google Ads customer discovery timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransportError(f"Google Ads customer discovery failed: {str(exc)[:200]}") from exc
        if resp.status_code in {401, 403}:
            raise CredentialsExpired("Google Ads credentials rejected during discovery")
        if resp.status_code == 429:
            raise ProviderRateLimited("Google Ads rate limited during discovery")
        if resp.status_code >= 400:
            raise ProviderTransportError(f"Google Ads discovery HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise MalformedProviderResponse("Google Ads discovery response was not JSON") from exc
        names = (body or {}).get("resourceNames") or []
        if not names:
            raise CredentialsMissing("Google Ads has no accessible customers")
        return str(names[0]).split("/")[-1]
