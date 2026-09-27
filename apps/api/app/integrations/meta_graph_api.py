"""Meta Graph API constants and shared helpers (M9.19).

Official API version verified: 2026-09-28
Source: https://developers.facebook.com/docs/graph-api/changelog/versions/
Latest release: v26.0 (2026-07-29). v25.0 supported until 2028-07-29.

GrowthOS uses v25.0 for a stable, documented sunset window. OAuth, Page discovery,
lead retrieval, ads sync, and webhooks share this versioned base URL unless a
specific endpoint documents a different requirement.
"""

from __future__ import annotations

META_GRAPH_API_VERSION = "v25.0"
META_GRAPH_API_BASE = f"https://graph.facebook.com/{META_GRAPH_API_VERSION}"
META_AUTH_URL = f"https://www.facebook.com/{META_GRAPH_API_VERSION}/dialog/oauth"
META_TOKEN_URL = f"{META_GRAPH_API_BASE}/oauth/access_token"

# Backward-compatible alias used across existing Meta integration modules.
META_GRAPH = META_GRAPH_API_BASE


def meta_graph_url(path: str) -> str:
    """Build a versioned Graph API URL from a path suffix or node id."""
    suffix = path if path.startswith("/") else f"/{path}"
    return f"{META_GRAPH_API_BASE}{suffix}"


def safe_meta_graph_error(text: str) -> str:
    """Redact credential-like substrings from provider error text."""
    lowered = (text or "").lower()
    for needle in (
        "access_token",
        "app_secret",
        "client_secret",
        "fb_exchange_token",
        "page_access_token",
    ):
        if needle in lowered:
            return "Meta Graph API error (details redacted)"
    return (text or "")[:240]
