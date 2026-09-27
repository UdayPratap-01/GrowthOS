"""Internal URL validation for the link engine (M9.12)."""

from __future__ import annotations

from urllib.parse import urlparse

from app.seo.normalize import is_same_site, normalize_url

BLOCKED_SCHEMES = frozenset({"javascript", "data", "file", "mailto", "tel"})
BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "metadata.google.internal",
        "169.254.169.254",
    }
)


def is_internal_url(url: str, *, site_root: str, include_subdomains: bool = False) -> bool:
    """Return True when url is a validated http(s) URL on the same site as site_root."""
    if not url or not site_root:
        return False
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() in BLOCKED_SCHEMES:
        return False
    host = (parsed.hostname or "").lower()
    if not host or host in BLOCKED_HOSTS:
        return False
    if host.startswith("127.") or host.startswith("10.") or host.startswith("192.168."):
        return False
    normalized = normalize_url(url, base_url=site_root)
    if not normalized:
        return False
    return is_same_site(normalized, site_root, include_subdomains=include_subdomains)


def normalize_internal_url(url: str, *, site_root: str) -> str | None:
    normalized = normalize_url(url, base_url=site_root)
    if not normalized:
        return None
    if not is_internal_url(normalized, site_root=site_root):
        return None
    return normalized
