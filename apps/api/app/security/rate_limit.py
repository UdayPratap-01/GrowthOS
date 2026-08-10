"""
Rate limiting.

Backends
--------
Redis is the production backend so that limits are shared across API instances;
an in-process backend exists for development and tests. Production startup fails
without `REDIS_URL` (see `app/core/startup_checks.py`) because per-process
counters give an attacker N times the budget on an N-instance deployment.

Keying
------
Credential endpoints are limited on **two** independent keys: the client IP and
a hash of the submitted account identifier. Both must pass. Limiting on the
identifier alone would let an attacker rotate email addresses to get unlimited
attempts; limiting on IP alone would let a distributed attacker hammer one
account. Requiring both closes each hole.

Client address
--------------
The IP half of that pair is only as good as the address it is derived from.
`X-Forwarded-For` is trusted exclusively when the request reached us through a
peer listed in `TRUSTED_PROXY_IPS`; otherwise the socket peer is used and the
header is ignored, so spoofing it buys nothing.

Account enumeration
-------------------
The limiter runs as a dependency, before any handler touches the database, so it
cannot know whether an account exists and therefore cannot behave differently.
Identifiers are hashed before being used as keys so no email is stored in Redis.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from typing import Protocol

from fastapi import HTTPException, Request, status

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


@dataclass(frozen=True)
class RateLimitPolicy:
    """A named budget: `limit` requests per `window_seconds`."""

    name: str
    limit: int
    window_seconds: int = 60

    def with_limit(self, limit: int) -> "RateLimitPolicy":
        return RateLimitPolicy(name=self.name, limit=limit, window_seconds=self.window_seconds)


class RateLimitBackend(Protocol):
    async def hit(self, key: str, policy: RateLimitPolicy) -> RateLimitResult: ...
    async def reset(self) -> None: ...
    async def ping(self) -> bool: ...


class InMemoryRateLimitBackend:
    """Per-process sliding window. Development and tests only."""

    name = "memory"

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    async def hit(self, key: str, policy: RateLimitPolicy) -> RateLimitResult:
        now = time.time()
        window_start = now - policy.window_seconds
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] < window_start:
                bucket.popleft()
            if len(bucket) >= policy.limit:
                retry_after = max(1, int(bucket[0] + policy.window_seconds - now) + 1)
                return RateLimitResult(False, policy.limit, 0, retry_after)
            bucket.append(now)
            return RateLimitResult(True, policy.limit, policy.limit - len(bucket), 0)

    async def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    async def ping(self) -> bool:
        return True


class RedisRateLimitBackend:
    """
    Shared fixed-window counter.

    INCR is atomic, so concurrent instances cannot both observe a
    below-limit count for the same request slot.
    """

    name = "redis"

    def __init__(self, url: str, *, fallback: InMemoryRateLimitBackend | None = None) -> None:
        import redis.asyncio as redis  # imported lazily so redis is optional in dev

        self._redis = redis.from_url(url, encoding="utf-8", decode_responses=True)
        self._fallback = fallback or InMemoryRateLimitBackend()
        self._degraded_logged = False

    async def hit(self, key: str, policy: RateLimitPolicy) -> RateLimitResult:
        window = policy.window_seconds
        slot = int(time.time() // window)
        redis_key = f"rl:{policy.name}:{key}:{slot}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(redis_key)
                pipe.expire(redis_key, window + 1)
                count, _ = await pipe.execute()
        except Exception as exc:
            return await self._degraded(key, policy, exc)

        self._degraded_logged = False
        count = int(count)
        if count > policy.limit:
            ttl = window - int(time.time() % window)
            return RateLimitResult(False, policy.limit, 0, max(1, ttl))
        return RateLimitResult(True, policy.limit, policy.limit - count, 0)

    async def _degraded(self, key: str, policy: RateLimitPolicy, exc: Exception) -> RateLimitResult:
        settings = get_settings()
        if not self._degraded_logged:
            logger.error(
                "Rate limit backend unavailable; %s",
                "degrading to per-process counters (limits are no longer shared across instances)"
                if settings.rate_limit_degrade_to_local
                else "failing closed",
                exc_info=exc,
            )
            self._degraded_logged = True
        if not settings.rate_limit_degrade_to_local:
            return RateLimitResult(False, policy.limit, 0, 5)
        return await self._fallback.hit(key, policy)

    async def reset(self) -> None:
        try:
            await self._redis.flushdb()
        except Exception:
            pass
        await self._fallback.reset()

    async def ping(self) -> bool:
        """
        Readiness probe. Reports the real state of Redis, not of the fallback:
        an instance silently counting on its own is exactly what the probe
        exists to reveal.
        """
        await self._redis.ping()
        return True


# --------------------------------------------------------------------------
# Backend selection
# --------------------------------------------------------------------------

_backend: RateLimitBackend | None = None


def get_rate_limit_backend() -> RateLimitBackend:
    global _backend
    if _backend is None:
        settings = get_settings()
        if settings.redis_url:
            _backend = RedisRateLimitBackend(settings.redis_url)
        else:
            _backend = InMemoryRateLimitBackend()
    return _backend


def set_rate_limit_backend(backend: RateLimitBackend | None) -> None:
    """Test seam; also used to swap backends after a configuration change."""
    global _backend
    _backend = backend


def is_shared_backend() -> bool:
    return getattr(get_rate_limit_backend(), "name", "memory") == "redis"


# --------------------------------------------------------------------------
# Policies
# --------------------------------------------------------------------------


def policies() -> dict[str, RateLimitPolicy]:
    s = get_settings()
    return {
        "general": RateLimitPolicy("general", s.rate_limit_per_minute, 60),
        "auth_identity": RateLimitPolicy("auth_identity", s.auth_rate_limit_per_minute, 60),
        "auth_ip": RateLimitPolicy("auth_ip", s.auth_rate_limit_per_ip_per_minute, 60),
        "ai": RateLimitPolicy("ai", s.ai_rate_limit_per_minute, 60),
        "media": RateLimitPolicy("media", s.media_rate_limit_per_hour, 3600),
        "report": RateLimitPolicy("report", s.report_rate_limit_per_hour, 3600),
        "campaign_execution": RateLimitPolicy(
            "campaign_execution", s.campaign_execution_rate_limit_per_minute, 60
        ),
        # Hourly, not per-minute: one generation can fan out into a dozen paid
        # image and video calls, so the budget that matters is the hourly one.
        "campaign_generation": RateLimitPolicy(
            "campaign_generation", s.campaign_generation_rate_limit_per_hour, 3600
        ),
        "webhook": RateLimitPolicy("webhook", s.webhook_rate_limit_per_minute, 60),
    }


def _too_many(result: RateLimitResult, scope: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        # Deliberately generic: the message must not reveal which key tripped,
        # or whether an account exists.
        detail="Rate limit exceeded. Please retry later.",
        headers={
            "Retry-After": str(result.retry_after),
            "X-RateLimit-Limit": str(result.limit),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Scope": scope,
        },
    )


async def enforce(key: str, policy: RateLimitPolicy, *, scope: str) -> RateLimitResult:
    result = await get_rate_limit_backend().hit(key, policy)
    if not result.allowed:
        from app.observability.metrics import record_rate_limited

        # The key itself is never logged: it contains a hashed identifier and,
        # for the IP policy, a client address.
        logger.warning(
            "Rate limit exceeded",
            extra={"event": "rate_limit.exceeded", "scope": scope, "policy": policy.name},
        )
        record_rate_limited(scope=scope)
        raise _too_many(result, scope)
    return result


#: Values of TRUSTED_PROXY_IPS that mean "there is no proxy in front of us".
_NO_PROXY = {"none", "off", "false", "disabled"}


@lru_cache(maxsize=8)
def _proxy_matcher(raw: str) -> tuple[tuple[object, ...], bool]:
    """
    Parse TRUSTED_PROXY_IPS into (networks, wildcard).

    Cached on the raw string: this runs on every request, and parsing CIDRs each
    time would be pure waste.
    """
    value = (raw or "").strip()
    if not value or value.lower() in _NO_PROXY:
        return (), False
    if value == "*":
        return (), True

    networks: list[object] = []
    for part in value.split(","):
        candidate = part.strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError:
            # Ignored rather than trusted: a typo must not widen the trust set.
            logger.error("Ignoring unparseable TRUSTED_PROXY_IPS entry %r", candidate)
    return tuple(networks), False


def _is_trusted_hop(address: str, networks: tuple[object, ...], wildcard: bool) -> bool:
    if wildcard:
        return True
    if not networks:
        return False
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in networks)  # type: ignore[operator]


def client_ip(request: Request) -> str:
    """
    The client address to key rate limits on.

    X-Forwarded-For is attacker-controlled unless the request demonstrably
    arrived through a proxy we configured, so the header is consulted only when
    the socket peer is in TRUSTED_PROXY_IPS. The chain is then walked from the
    right, skipping our own proxies, because everything to the left of the last
    trusted hop was supplied by the caller and can be forged.
    """
    peer = request.client.host if request.client else "unknown"
    networks, wildcard = _proxy_matcher(get_settings().trusted_proxy_ips)

    if not networks and not wildcard:
        return peer
    if not _is_trusted_hop(peer, networks, wildcard):
        return peer

    chain = [
        hop.strip()
        for hop in (request.headers.get("x-forwarded-for") or "").split(",")
        if hop.strip()
    ]
    if not chain:
        return peer
    if wildcard:
        # Development convenience: every peer is trusted, so there is no way to
        # tell our hops from the caller's. Take the client-most entry.
        return chain[0]
    for candidate in reversed(chain):
        if not _is_trusted_hop(candidate, networks, wildcard):
            return candidate
    return peer


def hash_identifier(value: str) -> str:
    """Hash account identifiers so no email address is written to Redis."""
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()[:32]


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------


async def rate_limit_dependency(request: Request) -> None:
    """General limit for authenticated traffic (attached to get_current_auth)."""
    auth = request.headers.get("authorization", "")
    key = f"{client_ip(request)}:{hash_identifier(auth) if auth else 'anon'}"
    await enforce(key, policies()["general"], scope="general")


async def auth_rate_limit(request: Request) -> None:
    """
    Credential endpoints: login, register, password reset, token refresh.

    Enforces the IP budget first, then the per-identifier budget. Both are
    incremented for every attempt regardless of whether the account exists.
    """
    ip = client_ip(request)
    limits = policies()
    await enforce(f"ip:{ip}", limits["auth_ip"], scope="auth_ip")

    # A browser refreshing with the httpOnly cookie sends no identifier in the
    # body, so the cookie stands in for one; without this, cookie clients would
    # only ever be limited per IP.
    identifier = await _identifier_from_body(request) or request.cookies.get("growthos_refresh")
    if identifier:
        await enforce(f"id:{hash_identifier(identifier)}", limits["auth_identity"], scope="auth_identity")


async def _identifier_from_body(request: Request) -> str | None:
    """
    Read the account identifier without consuming the stream.

    Starlette caches the body on the request, so the route handler still sees it.
    """
    try:
        raw = await request.body()
        if not raw:
            return None
        import json

        payload = json.loads(raw)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    for field in ("email", "username", "refresh_token"):
        value = payload.get(field)
        if isinstance(value, str) and value:
            return value
    return None


async def webhook_rate_limit(request: Request) -> None:
    await enforce(f"ip:{client_ip(request)}", policies()["webhook"], scope="webhook")


# Organization-scoped limits for expensive operations live in
# `app/security/limits.py`, which may import `app.core.deps` without a cycle.
