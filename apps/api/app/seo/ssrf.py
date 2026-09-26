"""SSRF protection for user-provided crawl URLs."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
    }
)


class SsrfError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return True
    if ip.is_global is False:
        return True
    # AWS/GCP metadata ranges
    if ip in ipaddress.ip_network("169.254.169.254/32"):
        return True
    return False


async def resolve_host_ips(host: str) -> list[str]:
    host = host.lower().strip(".")
    if host in BLOCKED_HOSTNAMES:
        raise SsrfError("blocked_hostname", f"Host {host!r} is not allowed")
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SsrfError("dns_failure", f"DNS lookup failed for {host!r}") from exc
    ips = sorted({info[4][0] for info in infos})
    if not ips:
        raise SsrfError("dns_failure", f"No addresses returned for {host!r}")
    return ips


async def validate_url_target(url: str, *, resolve=resolve_host_ips) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SsrfError("invalid_scheme", "Only http(s) URLs are allowed")
    host = (parsed.hostname or "").lower()
    if not host:
        raise SsrfError("invalid_url", "URL must include a hostname")
    if host in BLOCKED_HOSTNAMES:
        raise SsrfError("blocked_hostname", f"Host {host!r} is not allowed")
    for ip in await resolve(host):
        if is_blocked_ip(ip):
            raise SsrfError("blocked_ip", f"Resolved IP {ip} is not allowed")
