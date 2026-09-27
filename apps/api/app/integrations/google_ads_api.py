"""Google Ads REST API constants and shared helpers (M9.18).

Official API version verified: 2026-09-28
Source: https://developers.google.com/google-ads/api/docs/sunset-dates
Current stable major version: v25 (sunset August 2027; v25.2 released 2026-09-23).

Developer tokens were sunset 2026-09-09; API access levels are determined by the
Google Cloud project used for OAuth. The developer-token header remains optional
when configured for transitional compatibility.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import Settings, get_settings

# Verified against official sunset schedule on 2026-09-28.
GOOGLE_ADS_API_VERSION = "v25"
GOOGLE_ADS_API_BASE = f"https://googleads.googleapis.com/{GOOGLE_ADS_API_VERSION}"

ADS_SCOPE = "https://www.googleapis.com/auth/adwords"


def google_oauth_configured(settings: Settings | None = None) -> bool:
    """True when Google OAuth client credentials exist (required to connect)."""
    cfg = settings or get_settings()
    return bool(cfg.google_client_id and cfg.google_client_secret)


def google_ads_developer_token_configured(settings: Settings | None = None) -> bool:
    """True when a legacy/optional developer token is present in configuration."""
    cfg = settings or get_settings()
    return bool((cfg.google_ads_developer_token or "").strip())


def google_ads_api_ready(settings: Settings | None = None) -> bool:
    """True when OAuth is configured — sufficient to attempt Google Ads API calls."""
    return google_oauth_configured(settings)


def google_ads_headers(
    access_token: str,
    *,
    login_customer_id: str | None = None,
    content_type: bool = True,
    settings: Settings | None = None,
) -> dict[str, str]:
    """Build Google Ads REST headers. Never includes secrets beyond the bearer token."""
    cfg = settings or get_settings()
    headers: dict[str, str] = {"Authorization": f"Bearer {access_token}"}
    dev_token = (cfg.google_ads_developer_token or "").strip()
    if dev_token:
        headers["developer-token"] = dev_token
    if content_type:
        headers["Content-Type"] = "application/json"
    login = (login_customer_id or cfg.google_ads_login_customer_id or "").replace("-", "")
    if login:
        headers["login-customer-id"] = login
    return headers


def google_ads_url(path: str) -> str:
    """Build a versioned Google Ads REST URL from a path suffix."""
    suffix = path if path.startswith("/") else f"/{path}"
    return f"{GOOGLE_ADS_API_BASE}{suffix}"


def safe_google_ads_error(text: str) -> str:
    """Redact credential-like substrings from provider error text."""
    lowered = (text or "").lower()
    for needle in ("access_token", "refresh_token", "client_secret", "developer-token", "bearer "):
        if needle in lowered:
            return "Google Ads API error (details redacted)"
    return (text or "")[:240]


async def list_accessible_customers(
    access_token: str,
    *,
    http_client: httpx.AsyncClient | None = None,
    login_customer_id: str | None = None,
) -> list[str]:
    """Return raw resourceNames from listAccessibleCustomers."""
    owns = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30)
    try:
        resp = await client.get(
            google_ads_url("/customers:listAccessibleCustomers"),
            headers=google_ads_headers(access_token, login_customer_id=login_customer_id, content_type=False),
        )
    finally:
        if owns:
            await client.aclose()
    if resp.status_code >= 400:
        raise RuntimeError(f"Google customer discovery failed: {safe_google_ads_error(resp.text)}")
    return list((resp.json() if resp.content else {}).get("resourceNames") or [])


def sanitize_customers(resource_names: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in resource_names:
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
