"""Google Ads customer discovery helpers (M7/M9.18).

Mirrors Meta's meta_oauth discovery pattern. Never logs tokens or developer tokens.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.integrations.google_ads_api import (
    list_accessible_customers,
    safe_google_ads_error,
    sanitize_customers,
)


def _safe_google_error(text: str) -> str:
    return safe_google_ads_error(text)


async def discover_google_customers(
    access_token: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """List accessible Google Ads customers (sanitized ids only)."""
    try:
        names = await list_accessible_customers(access_token, http_client=http_client)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Google customer discovery failed: {_safe_google_error(str(exc))}") from exc
    return sanitize_customers(names)


def build_google_connection_config(
    *,
    customers: list[dict[str, Any]],
    preferred_customer_id: str | None = None,
) -> dict[str, Any]:
    """Sanitized Integration.config for Google Ads — never includes tokens."""
    preferred = (preferred_customer_id or "").replace("-", "") or None
    primary = None
    if preferred:
        primary = next((c for c in customers if str(c.get("id")) == preferred), None)
    if primary is None and customers:
        primary = customers[0]
    customer_id = (primary or {}).get("id")
    return {
        "account_label": (primary or {}).get("name") or (
            f"Google Ads / {customer_id}" if customer_id else "Google Ads (pending customer discovery)"
        ),
        "customer_id": customer_id,
        "external_account_id": customer_id,  # alias for canary allowlists
        "customers": [
            {
                "id": c.get("id"),
                "resource_name": c.get("resource_name"),
                "name": c.get("name"),
            }
            for c in customers[:50]
        ],
        "connected_at": datetime.now(timezone.utc).isoformat(),
        "discovery": {
            "customer_count": len(customers),
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def resolve_google_customer_id(
    *,
    campaign_metrics: dict | None,
    integration_config: dict | None,
    login_customer_id: str | None = None,
) -> str | None:
    """Prefer campaign metrics → integration config → MCC login id."""
    metrics = campaign_metrics or {}
    cfg = integration_config or {}
    for raw in (
        metrics.get("customer_id"),
        cfg.get("customer_id"),
        cfg.get("external_account_id"),
        login_customer_id,
    ):
        if raw:
            return str(raw).replace("-", "")
    return None
