"""Google Ads customer discovery helpers (M7).

Mirrors Meta's meta_oauth discovery pattern. Never logs tokens or developer tokens.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import get_settings

ADS_API = "https://googleads.googleapis.com/v18"


def _safe_google_error(text: str) -> str:
    lowered = (text or "").lower()
    for needle in ("access_token", "refresh_token", "client_secret", "developer-token", "bearer "):
        if needle in lowered:
            return "Google Ads API error (details redacted)"
    return (text or "")[:240]


def google_ads_headers(access_token: str, *, login_customer_id: str | None = None) -> dict[str, str]:
    settings = get_settings()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "developer-token": settings.google_ads_developer_token or "",
        "Content-Type": "application/json",
    }
    login = (login_customer_id or settings.google_ads_login_customer_id or "").replace("-", "")
    if login:
        headers["login-customer-id"] = login
    return headers


async def discover_google_customers(
    access_token: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """List accessible Google Ads customers (sanitized ids only)."""
    owns = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30)
    try:
        resp = await client.get(
            f"{ADS_API}/customers:listAccessibleCustomers",
            headers=google_ads_headers(access_token),
        )
    finally:
        if owns:
            await client.aclose()
    if resp.status_code >= 400:
        raise RuntimeError(f"Google customer discovery failed: {_safe_google_error(resp.text)}")
    names = (resp.json() if resp.content else {}).get("resourceNames") or []
    out: list[dict[str, Any]] = []
    for name in names:
        cid = str(name).split("/")[-1].replace("-", "")
        if not cid:
            continue
        out.append(
            {
                "id": cid,
                "resource_name": str(name),
                "name": f"Google Ads / {cid}",
            }
        )
    return out


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
