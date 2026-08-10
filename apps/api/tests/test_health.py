"""
P1-10 — liveness and readiness are different probes.

The property worth protecting is that liveness never depends on anything
external. A liveness probe that checks the database turns a recoverable database
outage into a restart loop across every pod, and the loop outlives the outage.
Several tests below break dependencies deliberately and assert that liveness
stays green while readiness goes red.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import health
from app.main import app


async def get(path: str):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        return await http.get(path)


# --------------------------------------------------------------------------
# Liveness
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_liveness_answers_without_authentication():
    resp = await get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


@pytest.mark.asyncio
async def test_liveness_reports_uptime():
    body = (await get("/health/live")).json()
    assert body["uptime_seconds"] >= 0


@pytest.mark.asyncio
async def test_liveness_survives_a_dead_database(monkeypatch):
    """The whole point: a database outage must not get every pod restarted."""

    async def explode() -> None:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(health, "_check_database", explode)

    resp = await get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


@pytest.mark.asyncio
async def test_liveness_survives_dead_storage(monkeypatch):
    async def explode() -> None:
        raise RuntimeError("bucket unreachable")

    monkeypatch.setattr(health, "_check_storage", explode)
    assert (await get("/health/live")).status_code == 200


@pytest.mark.asyncio
async def test_liveness_touches_no_dependency(monkeypatch):
    """Assert by construction, not by outcome: nothing external is called."""
    called: list[str] = []

    for name in ("_check_database", "_check_storage", "_check_queue", "_check_rate_limit_backend"):
        async def record(_name=name) -> None:
            called.append(_name)

        monkeypatch.setattr(health, name, record)

    await get("/health/live")
    assert called == []


# --------------------------------------------------------------------------
# Readiness
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readiness_passes_when_dependencies_are_up():
    resp = await get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_readiness_reports_every_check():
    checks = (await get("/health/ready")).json()["checks"]
    assert {"database", "configuration", "queue", "object_storage", "rate_limit_backend"} <= set(
        checks
    )


@pytest.mark.asyncio
async def test_readiness_fails_with_503_when_the_database_is_down(monkeypatch):
    async def explode() -> None:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(health, "_check_database", explode)

    resp = await get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "not_ready"
    assert resp.json()["checks"]["database"]["status"] == "failed"


@pytest.mark.asyncio
async def test_readiness_fails_when_storage_is_unreachable(monkeypatch):
    async def explode() -> None:
        raise RuntimeError("NoSuchBucket")

    monkeypatch.setattr(health, "_check_storage", explode)

    resp = await get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["checks"]["object_storage"]["status"] == "failed"


@pytest.mark.asyncio
async def test_readiness_fails_when_configuration_is_invalid(monkeypatch):
    async def explode() -> None:
        raise RuntimeError("SECRET_KEY is required in production")

    monkeypatch.setattr(health, "_check_configuration", explode)

    resp = await get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["checks"]["configuration"]["status"] == "failed"


@pytest.mark.asyncio
async def test_readiness_still_lists_healthy_checks_when_one_fails(monkeypatch):
    """An operator must see *which* dependency is down without reading logs."""

    async def explode() -> None:
        raise RuntimeError("down")

    monkeypatch.setattr(health, "_check_storage", explode)

    checks = (await get("/health/ready")).json()["checks"]
    assert checks["object_storage"]["status"] == "failed"
    assert checks["database"]["status"] == "ok"


@pytest.mark.asyncio
async def test_a_failure_detail_does_not_leak_internals(monkeypatch):
    """Connection errors carry DSNs. The type is enough for a probe."""

    async def explode() -> None:
        raise RuntimeError("could not connect to postgres://admin:hunter2@10.0.0.4:5432/prod")

    monkeypatch.setattr(health, "_check_database", explode)

    body = (await get("/health/ready")).text
    assert "hunter2" not in body
    assert "10.0.0.4" not in body
    assert "RuntimeError" in body


@pytest.mark.asyncio
async def test_a_hung_dependency_is_a_timeout_not_a_hang(monkeypatch):
    """A probe that hangs is indistinguishable from a dead process."""

    async def hang() -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(health, "_check_storage", hang)
    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.2)

    resp = await asyncio.wait_for(get("/health/ready"), timeout=5)
    assert resp.status_code == 503
    assert "Timed out" in resp.json()["checks"]["object_storage"]["detail"]


@pytest.mark.asyncio
async def test_checks_run_concurrently(monkeypatch):
    """Serial checks would multiply latency by the number of dependencies."""

    async def slow() -> None:
        await asyncio.sleep(0.3)

    for name in ("_check_database", "_check_storage", "_check_queue", "_check_configuration"):
        monkeypatch.setattr(health, name, slow)

    loop = asyncio.get_running_loop()
    started = loop.time()
    await get("/health/ready")
    elapsed = loop.time() - started

    assert elapsed < 0.9, "four 0.3s checks running in series would take 1.2s"


@pytest.mark.asyncio
async def test_an_optional_dependency_is_degraded_not_failed(monkeypatch):
    """
    Outside production the in-process rate limiter is a legitimate choice, so a
    missing Redis is reported without pulling the instance out of the pool.
    """

    async def explode() -> None:
        raise RuntimeError("redis down")

    monkeypatch.setattr(health, "_check_rate_limit_backend", explode)

    resp = await get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["degraded"] == ["rate_limit_backend"]
    assert resp.json()["checks"]["rate_limit_backend"]["required"] is False


@pytest.mark.asyncio
async def test_redis_is_required_for_readiness_in_production(monkeypatch):
    """In production the limiter is shared infrastructure, so it is required."""
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(type(settings), "is_production", property(lambda self: True))

    async def explode() -> None:
        raise RuntimeError("redis down")

    monkeypatch.setattr(health, "_check_rate_limit_backend", explode)

    resp = await get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["checks"]["rate_limit_backend"].get("required") is not False


@pytest.mark.asyncio
async def test_readiness_measures_each_check():
    checks = (await get("/health/ready")).json()["checks"]
    assert all("duration_ms" in check for check in checks.values())


@pytest.mark.asyncio
async def test_the_queue_check_reaches_the_job_table():
    """A readiness pass must mean the worker's claim query can actually run."""
    assert (await get("/health/ready")).json()["checks"]["queue"]["status"] == "ok"


@pytest.mark.asyncio
async def test_the_in_memory_limiter_reports_healthy():
    from app.security.rate_limit import get_rate_limit_backend

    assert await get_rate_limit_backend().ping() is True


# --------------------------------------------------------------------------
# Compatibility
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_original_health_endpoint_still_answers():
    """Existing probes and the compose healthcheck must not break."""
    resp = await get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_probes_are_not_behind_the_api_version_prefix():
    """Infrastructure should not have to track an API version to probe a pod."""
    assert (await get("/api/v1/health/live")).status_code == 404
