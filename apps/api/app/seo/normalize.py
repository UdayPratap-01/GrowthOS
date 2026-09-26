"""Deterministic URL normalization for crawl deduplication.

Policy
------
- Scheme and hostname are lowercased.
- Fragments are removed (never sent to servers).
- Default ports (:80 http, :443 https) are stripped.
- Paths are percent-decoded then re-encoded consistently.
- Trailing slashes on non-root paths are removed.
- Common tracking query params (utm_*, gclid, fbclid) are stripped.
- Other query parameters are preserved and sorted for stable dedupe keys.
- Relative and protocol-relative URLs are resolved against a base URL.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlparse, urlunparse

TRACKING_PARAMS = frozenset(
    {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid", "mc_cid", "mc_eid"}
)


def _normalize_path(path: str) -> str:
    if not path:
        return "/"
    segments = [quote(unquote(seg), safe="") for seg in path.split("/")]
    normalized = "/".join(segments)
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    if len(normalized) > 1 and normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    return normalized


def normalize_url(url: str, *, base_url: str | None = None) -> str | None:
    if not url or not str(url).strip():
        return None
    candidate = str(url).strip()
    if candidate.startswith("//") and base_url:
        base_scheme = urlparse(base_url).scheme or "https"
        candidate = f"{base_scheme}:{candidate}"
    if base_url and not candidate.startswith(("http://", "https://")):
        candidate = urljoin(base_url, candidate)
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    host = parsed.hostname or ""
    if not host:
        return None
    host = host.lower()
    port = parsed.port
    if (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443):
        port = None
    netloc = host if port is None else f"{host}:{port}"
    query_pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
    query_pairs.sort(key=lambda item: (item[0], item[1]))
    query = urlencode(query_pairs)
    return urlunparse((parsed.scheme.lower(), netloc, _normalize_path(parsed.path), "", query, ""))


def registrable_domain(host: str) -> str:
    host = (host or "").lower().strip(".")
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    return ".".join(parts[-2:])


def is_same_site(url: str, root_url: str, *, include_subdomains: bool = False) -> bool:
    a = urlparse(url)
    b = urlparse(root_url)
    if a.scheme not in {"http", "https"} or b.scheme not in {"http", "https"}:
        return False
    host_a = (a.hostname or "").lower()
    host_b = (b.hostname or "").lower()
    if not host_a or not host_b:
        return False
    if host_a == host_b:
        return True
    if include_subdomains:
        return host_a.endswith(f".{host_b}") or host_b.endswith(f".{host_a}")
    return False
