"""Meta Ads insights → NormalizedPerformanceRow (read-only)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx

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
from app.integrations.meta_family import META_GRAPH
from app.integrations.persistence import get_integration_row, load_tokens
from sqlalchemy.ext.asyncio import AsyncSession

_LEAD_ACTION_TYPES = {
    "lead",
    "onsite_conversion.messaging_conversation_started_7d",
    "onsite_conversion.lead_grouped",
    "leadgen_grouped",
}
_PURCHASE_ACTION_TYPES = {
    "purchase",
    "omni_purchase",
    "offsite_conversion.fb_pixel_purchase",
}


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


def _actions_sum(actions: list | None, types: set[str]) -> Decimal:
    total = Decimal("0")
    for action in actions or []:
        if not isinstance(action, dict):
            continue
        if action.get("action_type") in types:
            total += _safe_decimal(action.get("value"))
    return total


def normalize_meta_insight_row(
    *,
    organization_id: UUID,
    client_id: UUID | None,
    account_id: str,
    row: dict[str, Any],
    entity_level: str = "campaign",
) -> NormalizedPerformanceRow | None:
    """Map one Meta insights row into the normalized model. Returns None if unusable."""
    if not isinstance(row, dict):
        return None
    day_raw = row.get("date_start") or row.get("date_stop")
    if not day_raw:
        return None
    try:
        day = date.fromisoformat(str(day_raw)[:10])
    except ValueError:
        return None

    campaign_id = str(row.get("campaign_id") or "")
    adset_id = str(row.get("adset_id") or "")
    ad_id = str(row.get("ad_id") or "")
    if entity_level == "campaign" and not campaign_id:
        # Account-level insights omit campaign_id; treat as account grain.
        entity_level = "account"

    actions = row.get("actions") if isinstance(row.get("actions"), list) else []
    action_values = row.get("action_values") if isinstance(row.get("action_values"), list) else []
    leads = int(_actions_sum(actions, _LEAD_ACTION_TYPES))
    conversions = _actions_sum(actions, _LEAD_ACTION_TYPES | _PURCHASE_ACTION_TYPES)
    revenue = _actions_sum(action_values, _PURCHASE_ACTION_TYPES)

    return NormalizedPerformanceRow(
        organization_id=organization_id,
        client_id=client_id,
        platform="meta",
        entity_level=entity_level,
        date=day,
        external_account_id=str(account_id or ""),
        external_campaign_id=campaign_id,
        external_ad_set_id=adset_id,
        external_ad_id=ad_id,
        impressions=_safe_int(row.get("impressions")),
        reach=_safe_int(row.get("reach")) if row.get("reach") is not None else None,
        clicks=_safe_int(row.get("clicks")),
        spend=_safe_decimal(row.get("spend")),
        conversions=conversions,
        leads=leads,
        revenue=revenue,
        currency="USD",
        provider_metadata={
            "source": "meta_insights",
            "campaign_name": row.get("campaign_name"),
            "account_id": account_id,
            "date_start": row.get("date_start"),
            "date_stop": row.get("date_stop"),
        },
    )


class MetaInsightsFetcher:
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
        row = await get_integration_row(
            self.db, organization_id=organization_id, provider="meta", client_id=client_id
        )
        if not row or not row.secret_ref:
            row = await get_integration_row(
                self.db, organization_id=organization_id, provider="meta", client_id=None
            )
        if not row:
            raise IntegrationDisconnected("Meta integration is not connected")
        if row.status != "connected" or not row.secret_ref:
            raise IntegrationDisconnected("Meta integration is not connected")

        tokens = load_tokens(row) or {}
        access_token = tokens.get("access_token")
        if not access_token:
            raise CredentialsMissing("Meta access token is missing")

        end = date.today()
        start = end - timedelta(days=max(1, lookback_days) - 1)

        try:
            async with httpx.AsyncClient(timeout=45) as client:
                accounts_resp = await client.get(
                    f"{META_GRAPH}/me/adaccounts",
                    params={"access_token": access_token, "fields": "id,name,account_id"},
                )
                if accounts_resp.status_code == 401:
                    raise CredentialsExpired("Meta access token rejected")
                if accounts_resp.status_code == 429:
                    raise ProviderRateLimited("Meta adaccounts rate limited")
                if accounts_resp.status_code >= 400:
                    raise ProviderTransportError(
                        f"Meta adaccounts HTTP {accounts_resp.status_code}"
                    )
                try:
                    accounts_body = accounts_resp.json()
                except ValueError as exc:
                    raise MalformedProviderResponse("Meta adaccounts response was not JSON") from exc
                accounts = accounts_body.get("data") if isinstance(accounts_body, dict) else None
                if accounts is None:
                    raise MalformedProviderResponse("Meta adaccounts missing data array")

                rows: list[NormalizedPerformanceRow] = []
                for account in accounts:
                    if not isinstance(account, dict):
                        continue
                    account_id = str(account.get("id") or "")
                    if not account_id:
                        continue
                    fields = (
                        "campaign_id,campaign_name,adset_id,ad_id,spend,impressions,reach,"
                        "clicks,actions,action_values,date_start,date_stop"
                    )
                    params = {
                        "access_token": access_token,
                        "time_range": json.dumps({"since": start.isoformat(), "until": end.isoformat()}),
                        "time_increment": 1,
                        "level": entity_level if entity_level in {"campaign", "adset", "ad", "ad_set"} else "campaign",
                        "fields": fields,
                    }
                    # Meta uses "adset" not "ad_set"
                    if params["level"] == "ad_set":
                        params["level"] = "adset"

                    insights_resp = await client.get(
                        f"{META_GRAPH}/{account_id}/insights",
                        params=params,
                    )
                    if insights_resp.status_code == 401:
                        raise CredentialsExpired("Meta access token rejected during insights")
                    if insights_resp.status_code == 429:
                        raise ProviderRateLimited("Meta insights rate limited")
                    if insights_resp.status_code >= 400:
                        raise ProviderTransportError(
                            f"Meta insights HTTP {insights_resp.status_code}"
                        )
                    try:
                        insights_body = insights_resp.json()
                    except ValueError as exc:
                        raise MalformedProviderResponse("Meta insights response was not JSON") from exc
                    if not isinstance(insights_body, dict):
                        raise MalformedProviderResponse("Meta insights body was not an object")
                    for item in insights_body.get("data") or []:
                        mapped_level = entity_level
                        if mapped_level == "adset":
                            mapped_level = "ad_set"
                        normalized = normalize_meta_insight_row(
                            organization_id=organization_id,
                            client_id=client_id,
                            account_id=account_id,
                            row=item if isinstance(item, dict) else {},
                            entity_level=mapped_level,
                        )
                        if normalized:
                            rows.append(normalized)
                return rows
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("Meta insights request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransportError(f"Meta insights transport error: {str(exc)[:200]}") from exc
