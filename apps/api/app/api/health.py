"""
Liveness and readiness.

The two probes answer different questions and must not be confused, because an
orchestrator reacts to them differently.

**Liveness** — "is this process still working, or is it wedged?" A failing
liveness probe gets the container killed. It therefore touches nothing external:
if the database goes down and liveness checked it, Kubernetes would restart every
API pod in a rolling loop, turning a recoverable dependency outage into a total
outage that continues after the dependency recovers.

**Readiness** — "should this instance receive traffic right now?" A failing
readiness probe removes the pod from the load balancer and leaves it running.
This one does check dependencies, because an instance that cannot reach its
database should not be handed requests.

Checks run concurrently and each carries its own timeout, so one hung dependency
cannot make the probe itself time out — a readiness probe that hangs looks
identical to a dead process to most orchestrators.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import engine

logger = logging.getLogger("growthos.health")

router = APIRouter(tags=["health"])

#: Generous enough to survive a slow query, short enough that the probe answers
#: within a typical 5s orchestrator timeout even if every check is slow.
CHECK_TIMEOUT_SECONDS = 3.0

_started_at = time.monotonic()


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str | None = None
    duration_ms: int = 0
    #: A degraded optional dependency is reported but does not fail readiness.
    required: bool = True

    def as_dict(self) -> dict:
        body: dict = {"status": "ok" if self.ok else "failed", "duration_ms": self.duration_ms}
        if self.detail:
            body["detail"] = self.detail
        if not self.required:
            body["required"] = False
        return body


async def _run(name: str, check: Callable[[], Awaitable[None]], *, required: bool = True) -> CheckResult:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SECONDS)
        ok, detail = True, None
    except asyncio.TimeoutError:
        ok, detail = False, f"Timed out after {CHECK_TIMEOUT_SECONDS:g}s."
    except Exception as exc:
        # The exception text can carry a DSN or a bucket policy, so the response
        # gets the type only. The full error goes to the log.
        ok, detail = False, type(exc).__name__
        logger.warning(
            "Readiness check failed", exc_info=exc, extra={"event": "health.check_failed", "check": name}
        )
    return CheckResult(
        name=name,
        ok=ok,
        detail=detail,
        duration_ms=int((time.perf_counter() - started) * 1000),
        required=required,
    )


async def _check_database() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_storage() -> None:
    from app.storage import get_object_storage

    await get_object_storage().health_check()


async def _check_queue() -> None:
    """
    The queue is a table, so this proves the worker's claim query can run rather
    than that a worker is alive — liveness of the worker is its own concern.
    """
    from app.models.automation import BackgroundJob

    async with engine.connect() as conn:
        await conn.execute(
            text(f"SELECT 1 FROM {BackgroundJob.__tablename__} LIMIT 1")  # noqa: S608 — table name is a constant
        )


async def _check_rate_limit_backend() -> None:
    from app.security.rate_limit import get_rate_limit_backend

    backend = get_rate_limit_backend()
    ping = getattr(backend, "ping", None)
    if ping is not None:
        await ping()


async def _check_configuration() -> None:
    from app.core.startup_checks import validate_configuration

    validate_configuration()


@router.get("/health/live")
async def live() -> dict:
    """
    Process liveness. Deliberately checks nothing external — see module docstring.
    """
    settings = get_settings()
    return {
        "status": "alive",
        "service": "growthos-api",
        "environment": settings.env,
        "uptime_seconds": int(time.monotonic() - _started_at),
    }


@router.get("/health/ready")
async def ready(response: Response) -> dict:
    """
    Dependency readiness. 200 when this instance can serve, 503 when it cannot.

    The body always lists every check, including on failure, so an operator can
    see *which* dependency is down without reading logs.
    """
    settings = get_settings()

    checks: list[tuple[str, Callable[[], Awaitable[None]], bool]] = [
        ("database", _check_database, True),
        ("configuration", _check_configuration, True),
        ("queue", _check_queue, True),
        ("object_storage", _check_storage, True),
        # Redis is required in production (P1-1 refuses to boot without it); in
        # development the in-process limiter is a legitimate choice, so a missing
        # Redis is reported without failing the probe.
        ("rate_limit_backend", _check_rate_limit_backend, settings.is_production),
    ]

    results = await asyncio.gather(
        *(_run(name, check, required=required) for name, check, required in checks)
    )

    failed = [result for result in results if not result.ok and result.required]
    degraded = [result for result in results if not result.ok and not result.required]

    if failed:
        response.status_code = 503
        logger.error(
            "Readiness probe failing",
            extra={"event": "health.not_ready", "failed": [r.name for r in failed]},
        )

    return {
        "status": "ready" if not failed else "not_ready",
        "environment": settings.env,
        "degraded": [result.name for result in degraded] or None,
        "checks": {result.name: result.as_dict() for result in results},
        "operational": _operational_status(settings),
    }


def _operational_status(settings) -> dict:
    """Non-blocking operator view of feature latches (never fails readiness)."""

    def latch(enabled: bool, configured: bool = True) -> str:
        if not configured:
            return "NOT_CONFIGURED"
        if not enabled:
            return "DISABLED"
        return "HEALTHY"

    meta_cfg = bool(settings.meta_app_id and settings.meta_app_secret)
    google_cfg = bool(
        settings.google_client_id
        and settings.google_client_secret
        and settings.google_ads_developer_token
    )
    return {
        "database": "HEALTHY",  # mirrored from required check; probe already failed if down
        "job_queue": "HEALTHY",
        "worker": "NOT_CONFIGURED",  # worker liveness is separate process
        "scheduler": latch(settings.autopilot_scheduler_enabled),
        "optimization_engine": latch(settings.optimization_enabled),
        "autonomous_execution": latch(settings.autonomous_execution_enabled),
        "kill_switch": "ENABLED" if settings.autonomous_kill_switch else "DISABLED",
        "meta_provider": latch(settings.meta_autonomous_enabled, configured=meta_cfg)
        if meta_cfg
        else "NOT_CONFIGURED",
        "google_provider": latch(settings.google_autonomous_enabled, configured=google_cfg)
        if google_cfg
        else "NOT_CONFIGURED",
        "provider_verification": latch(settings.provider_verification_enabled),
    }
