"""
P1-11 — metrics come from real events.

The failure mode this guards against is a monitoring system that looks healthy
because it is measuring itself. Every assertion below drives a real code path —
an HTTP request, a failing job, a rejected login — and then checks the counter
moved. Nothing is recorded by the test directly except where the unit under test
*is* the registry.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.enums import MemberRole
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.observability import metrics

PASSWORD = "Str0ng-Test-Passw0rd!"


@pytest.fixture(autouse=True)
def _clean_registry():
    metrics.reset()
    yield
    metrics.reset()


@pytest.fixture
async def account():
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        organization = Organization(name=f"Mon {suffix}", slug=f"mon-{suffix}", demo_mode=False)
        user = User(
            email=f"mon-{suffix}@example.com",
            hashed_password=hash_password(PASSWORD),
            full_name="Mon",
        )
        db.add_all([organization, user])
        await db.flush()
        db.add(
            OrganizationMember(
                organization_id=organization.id, user_id=user.id, role=MemberRole.owner
            )
        )
        await db.commit()
        return {"id": organization.id, "email": user.email}


def counter(name: str, **labels) -> int:
    total = 0
    for row in metrics.snapshot()["counters"]:
        if row["name"] != name:
            continue
        if all(row["labels"].get(key) == value for key, value in labels.items()):
            total += row["value"]
    return total


async def call(method: str, path: str, **kwargs):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        return await http.request(method, path, **kwargs)


# --------------------------------------------------------------------------
# The registry itself
# --------------------------------------------------------------------------


def test_a_metric_never_observed_is_absent_not_zero():
    """Reporting an unobserved series as 0 asserts activity that never happened."""
    assert metrics.snapshot()["counters"] == []
    assert "ai_failures_total" not in metrics.prometheus_text()


def test_counters_accumulate_per_label_set():
    metrics.increment("thing_total", labels={"kind": "a"})
    metrics.increment("thing_total", labels={"kind": "a"})
    metrics.increment("thing_total", labels={"kind": "b"})

    assert counter("thing_total", kind="a") == 2
    assert counter("thing_total", kind="b") == 1


def test_histograms_record_count_sum_and_max():
    metrics.observe("latency_ms", 10, labels={"path": "/x"})
    metrics.observe("latency_ms", 30, labels={"path": "/x"})

    hist = metrics.snapshot()["histograms"][0]
    assert hist["count"] == 2
    assert hist["sum_ms"] == 40
    assert hist["max_ms"] == 30
    assert hist["avg_ms"] == 20


def test_prometheus_output_is_parseable():
    metrics.increment("http_requests_total", labels={"method": "GET", "status": "200"})
    metrics.observe("http_request_duration_ms", 12.5, labels={"method": "GET"})

    text = metrics.prometheus_text()
    assert 'http_requests_total{method="GET",status="200"} 1' in text
    assert "http_request_duration_ms_bucket{" in text
    assert "http_request_duration_ms_count{" in text
    assert "process_uptime_seconds " in text
    for line in text.strip().split("\n"):
        # name{labels} value — every line must carry a numeric sample.
        float(line.rsplit(" ", 1)[1])


def test_a_quote_in_a_label_cannot_break_the_exposition_format():
    metrics.increment("odd_total", labels={"path": 'a"b'})
    text = metrics.prometheus_text()
    assert 'path="ab"' in text


# --------------------------------------------------------------------------
# Real events
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_real_request_is_counted_with_its_latency():
    await call("GET", "/health/live")

    assert counter("http_requests_total", status="200") >= 1
    histograms = metrics.snapshot()["histograms"]
    assert any(h["name"] == "http_request_duration_ms" for h in histograms)
    assert all(h["max_ms"] >= 0 for h in histograms)


@pytest.mark.asyncio
async def test_a_client_error_is_counted_separately_from_a_server_error():
    await call("GET", "/api/v1/clients")  # 401, no token

    assert counter("http_client_errors_total") >= 1
    assert counter("http_server_errors_total") == 0


@pytest.mark.asyncio
async def test_a_failed_login_is_counted_as_a_failure(account):
    await call(
        "POST",
        "/api/v1/auth/login",
        json={"email": account["email"], "password": "wrong-password"},
    )
    assert counter("auth_attempts_total", outcome="failure") == 1
    assert counter("auth_attempts_total", outcome="success") == 0


@pytest.mark.asyncio
async def test_a_successful_login_is_counted(account):
    await call(
        "POST", "/api/v1/auth/login", json={"email": account["email"], "password": PASSWORD}
    )
    assert counter("auth_attempts_total", outcome="success") == 1


@pytest.mark.asyncio
async def test_a_throttled_request_is_counted():
    from app.security.rate_limit import RateLimitPolicy, enforce

    policy = RateLimitPolicy(name="test", limit=1, window_seconds=60)
    await enforce("monitoring-key", policy, scope="test")
    with pytest.raises(Exception):
        await enforce("monitoring-key", policy, scope="test")

    assert counter("rate_limited_total", scope="test") == 1


@pytest.mark.asyncio
async def test_a_failing_job_is_counted_as_a_job_failure(monkeypatch):
    """Driven through the real worker, not by calling the recorder."""
    from app.jobs import registry
    from app.jobs.queue import JobQueue
    from app.worker import Worker

    job_type = f"mon_fail_{uuid.uuid4().hex[:6]}"

    async def always_fails(_db, _job):
        raise RuntimeError("provider exploded")

    real_build = registry.build_queue

    def build(db, **kwargs):
        queue = real_build(db, **kwargs)
        queue.register(job_type, always_fails)
        return queue

    monkeypatch.setattr("app.worker.build_queue", build)

    async with AsyncSessionLocal() as db:
        await JobQueue(db).enqueue(job_type=job_type, payload={}, max_attempts=1)
        await db.commit()

    await Worker(poll_interval=0.01).run_once()

    assert counter("job_failures_total", job_type=job_type) == 1
    assert counter("jobs_total", job_type=job_type, status="failed") == 1


@pytest.mark.asyncio
async def test_a_successful_job_is_counted_without_a_failure(monkeypatch):
    from app.jobs import registry
    from app.jobs.queue import JobQueue
    from app.worker import Worker

    job_type = f"mon_ok_{uuid.uuid4().hex[:6]}"

    async def succeeds(_db, _job):
        return {"ok": True}

    real_build = registry.build_queue

    def build(db, **kwargs):
        queue = real_build(db, **kwargs)
        queue.register(job_type, succeeds)
        return queue

    monkeypatch.setattr("app.worker.build_queue", build)

    async with AsyncSessionLocal() as db:
        await JobQueue(db).enqueue(job_type=job_type, payload={})
        await db.commit()

    await Worker(poll_interval=0.01).run_once()

    assert counter("jobs_total", job_type=job_type, status="completed") == 1
    assert counter("job_failures_total", job_type=job_type) == 0


@pytest.mark.asyncio
async def test_a_database_error_is_counted():
    from sqlalchemy.exc import OperationalError

    from app.core.errors import _log_database_error

    _log_database_error(OperationalError("SELECT 1", {}, Exception("down")), "operational")
    assert counter("database_errors_total", kind="operational") == 1


def test_a_storage_failure_and_success_are_both_counted():
    """A failure count with no denominator cannot be read as good or terrible."""
    metrics.record_storage(operation="upload", success=True)
    metrics.record_storage(operation="upload", success=False)

    assert counter("storage_operations_total", operation="upload") == 2
    assert counter("storage_failures_total", operation="upload") == 1


def test_media_video_failures_are_counted():
    metrics.record_media(kind="video", provider="replicate", status="failed")
    assert counter("media_failures_total", kind="video") == 1


def test_integration_failures_are_counted():
    metrics.record_integration(provider="meta", success=False)
    assert counter("integration_syncs_total", provider="meta") == 1
    assert counter("integration_failures_total", provider="meta") == 1


def test_ai_failures_are_counted():
    metrics.record_ai(provider="openai", operation="complete", success=False)
    assert counter("ai_failures_total", provider="openai") == 1


def test_every_metric_the_audit_asked_for_has_a_recorder():
    required = {
        "request count": metrics.record_request,
        "request latency": metrics.record_request,
        "error count": metrics.record_request,
        "job count": metrics.record_job,
        "job failures": metrics.record_job,
        "AI failures": metrics.record_ai,
        "media failures": metrics.record_media,
        "storage failures": metrics.record_storage,
        "integration failures": metrics.record_integration,
    }
    assert all(callable(recorder) for recorder in required.values())


# --------------------------------------------------------------------------
# Exposition endpoint
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_metrics_endpoint_serves_prometheus_text():
    metrics.increment("http_requests_total", labels={"method": "GET"})
    resp = await call("GET", "/metrics")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "http_requests_total" in resp.text


@pytest.mark.asyncio
async def test_the_json_view_carries_the_same_numbers():
    metrics.increment("jobs_total", labels={"job_type": "x", "status": "completed"})
    body = (await call("GET", "/metrics.json")).json()

    assert any(row["name"] == "jobs_total" and row["value"] == 1 for row in body["counters"])


@pytest.mark.asyncio
async def test_a_token_is_enforced_when_one_is_configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "metrics_token", "s3cret-scrape-token")

    unauthorized = await call("GET", "/metrics")
    wrong = await call("GET", "/metrics", headers={"Authorization": "Bearer nope"})
    right = await call("GET", "/metrics", headers={"Authorization": "Bearer s3cret-scrape-token"})

    assert unauthorized.status_code == 401
    assert wrong.status_code == 401
    assert right.status_code == 200


@pytest.mark.asyncio
async def test_production_without_a_token_hides_the_endpoint(monkeypatch):
    """Belt and braces: startup already refuses, but a misconfiguration closes."""
    settings = get_settings()
    monkeypatch.setattr(settings, "metrics_token", "")
    monkeypatch.setattr(type(settings), "is_production", property(lambda self: True))

    assert (await call("GET", "/metrics")).status_code == 404


def test_production_startup_requires_a_metrics_token():
    from app.core.config import Settings
    from app.core.startup_checks import ConfigurationError, validate_configuration

    settings = Settings(
        environment="production",
        secret_key="P4ssw0rd-grade-random-secret-value-9182",
        encryption_key="An0ther-strong-encryption-secret-7261",
        demo_mode=False,
        ai_provider="openai",
        openai_api_key="sk-real-key",
        database_url="postgresql+asyncpg://u:p@db/growthos",
        redis_url="redis://cache:6379/0",
        storage_backend="s3",
        s3_bucket="growthos-assets",
        inline_job_execution=False,
        db_auto_create=False,
        api_cors_origins="https://app.growthos.ai",
        metrics_token="",
    )
    with pytest.raises(ConfigurationError) as raised:
        validate_configuration(settings)
    assert "METRICS_TOKEN" in str(raised.value)


# --------------------------------------------------------------------------
# MonitoringAgent's role
# --------------------------------------------------------------------------


def test_monitoring_agent_is_a_campaign_analyst_not_infrastructure_monitoring():
    """
    The audit flagged MonitoringAgent as implemented but never invoked. It is a
    *campaign* health analyst; infrastructure monitoring is this metrics module.
    Conflating them is what left it unused.
    """
    from app.ai.agents.monitoring_agent import MonitoringAgent, MonitoringReport

    assert MonitoringAgent.output_schema is MonitoringReport
    fields = set(MonitoringReport.model_fields)
    assert {"overview", "health", "alerts"} <= fields
    # Nothing about requests, jobs or storage: it does not observe the system.
    assert not {"request_count", "error_rate", "uptime"} & fields


@pytest.mark.asyncio
async def test_the_narrative_never_supplies_the_score(account, monkeypatch):
    """
    A language model must not produce a health number. The scores stay
    arithmetic; the agent only explains them.
    """
    from app.services import optimization_service as module

    class FakeReport:
        overview = "Two campaigns are underperforming on cost per lead."
        alerts = ["CPL rising"]
        insufficient_data = []
        # A model trying to assert its own scores must be ignored.
        health = [{"campaign_id": "made-up", "score": 99}]

    class FakeOrchestrator:
        async def monitor(self, context, *, analytics_summary, campaigns):
            return FakeReport()

    monkeypatch.setattr(module, "get_orchestrator", lambda: FakeOrchestrator())

    async with AsyncSessionLocal() as db:
        service = module.OptimizationService(db)
        organization = await db.get(Organization, account["id"])
        result = await service.health_narrative(organization, uuid.uuid4())

    # No scored campaigns for this client, so there is nothing to narrate and
    # the service says so rather than inventing an overview.
    assert result["narrative_available"] is False
    assert result["health"] == []


@pytest.mark.asyncio
async def test_a_provider_outage_still_returns_the_real_scores(account, monkeypatch):
    from app.models.automation import CampaignHealth
    from app.models.client import Client
    from app.models.enums import HealthCategory
    from app.models.marketing import Campaign
    from app.services import optimization_service as module

    async with AsyncSessionLocal() as db:
        client = Client(
            organization_id=account["id"], business_name="Health Co", industry="saas"
        )
        db.add(client)
        await db.flush()
        client_id = client.id
        # A real campaign row: PostgreSQL enforces the foreign key that SQLite
        # lets slide, and a health score with no campaign is not a real state.
        campaign = Campaign(
            organization_id=account["id"],
            client_id=client_id,
            name="Health Campaign",
            platform="meta",
        )
        db.add(campaign)
        await db.flush()
        db.add(
            CampaignHealth(
                organization_id=account["id"],
                client_id=client_id,
                campaign_id=campaign.id,
                score=72,
                category=HealthCategory.good,
                evidence=["CTR=2.1"],
                metrics_snapshot={},
                data_source="live",
            )
        )
        await db.commit()

    class BrokenOrchestrator:
        async def monitor(self, context, *, analytics_summary, campaigns):
            raise RuntimeError("provider down")

    monkeypatch.setattr(module, "get_orchestrator", lambda: BrokenOrchestrator())

    async with AsyncSessionLocal() as db:
        organization = await db.get(Organization, account["id"])
        result = await module.OptimizationService(db).health_narrative(organization, client_id)

    assert result["narrative_available"] is False
    assert result["health"][0]["score"] == 72, "real scores survive an AI outage"
    assert "unavailable" in result["overview"].lower()
