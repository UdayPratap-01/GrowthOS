"""P1-4 — structured logging, correlation IDs and secret redaction."""

from __future__ import annotations

import json
import logging
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.enums import MemberRole
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.observability import events
from app.observability.logging import (
    ContextFilter,
    HumanFormatter,
    JsonFormatter,
    bind_request_context,
    clear_request_context,
    configure_logging,
    is_sensitive_key,
    redact,
)

PASSWORD = "Str0ng-Test-Passw0rd!"


@pytest.fixture(autouse=True)
def _clear_context():
    clear_request_context()
    yield
    clear_request_context()


def _emit(logger_name: str, level: int, message: str, **extra) -> dict:
    """Capture exactly one record as the JSON formatter would render it."""
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Capture()
    handler.addFilter(ContextFilter())
    logger = logging.getLogger(logger_name)
    previous_handlers, previous_propagate, previous_level = (
        logger.handlers,
        logger.propagate,
        logger.level,
    )
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    try:
        logger.log(level, message, extra=extra)
    finally:
        logger.handlers, logger.propagate, logger.level = (
            previous_handlers,
            previous_propagate,
            previous_level,
        )
    assert records, "no log record captured"
    return json.loads(JsonFormatter(service="test").format(records[-1]))


# --------------------------------------------------------------------------
# Format
# --------------------------------------------------------------------------


def test_production_logs_are_json_with_the_expected_envelope():
    payload = _emit("growthos.test", logging.INFO, "hello", event="unit.test", count=3)
    assert payload["level"] == "INFO"
    assert payload["message"] == "hello"
    assert payload["service"] == "test"
    assert payload["event"] == "unit.test"
    assert payload["count"] == 3
    assert "timestamp" in payload


def test_json_log_line_is_a_single_parseable_object():
    """Multi-line output breaks line-oriented log shippers."""
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "multi\nline", (), None)
    rendered = JsonFormatter(service="test").format(record)
    assert "\n" not in rendered
    assert json.loads(rendered)["message"] == "multi\nline"


def test_exception_detail_goes_to_the_log_not_the_response():
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("growthos.test.exc")
    logger.handlers = [Capture()]
    logger.propagate = False
    try:
        raise ValueError("internal detail")
    except ValueError:
        logger.exception("boom")

    payload = json.loads(JsonFormatter(service="test").format(records[-1]))
    assert "internal detail" in payload["exception"]
    assert "Traceback" in payload["exception"]


def test_format_defaults_to_text_in_development_and_json_elsewhere():
    assert Settings(environment="development").log_format == ""
    configure_logging(force=True)
    handler = logging.getLogger().handlers[0]
    expected = HumanFormatter if get_settings().is_development else JsonFormatter
    assert isinstance(handler.formatter, expected)


# --------------------------------------------------------------------------
# Redaction — the property that matters most
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "Password",
        "user_password",
        "secret_key",
        "META_APP_SECRET",
        "access_token",
        "refresh_token",
        "api_key",
        "OPENAI_API_KEY",
        "authorization",
        "credentials",
        "private_key",
        "s3_secret_access_key",
        "cookie",
        "signature",
    ],
)
def test_sensitive_keys_are_recognised(key):
    assert is_sensitive_key(key), f"{key} must be treated as sensitive"


def test_redaction_reaches_nested_structures():
    payload = {
        "user": {"email": "a@b.com", "password": "hunter2"},
        "integrations": [{"provider": "meta", "access_token": "EAAG-secret"}],
        "safe": "keep me",
    }
    cleaned = redact(payload)
    assert cleaned["user"]["password"] == "[REDACTED]"
    assert cleaned["integrations"][0]["access_token"] == "[REDACTED]"
    assert cleaned["user"]["email"] == "a@b.com"
    assert cleaned["safe"] == "keep me"


def test_secret_passed_as_a_log_field_never_reaches_the_output():
    payload = _emit(
        "growthos.test",
        logging.INFO,
        "connecting",
        provider="meta",
        access_token="EAAG-super-secret-value",
        config={"password": "hunter2", "host": "db"},
    )
    rendered = json.dumps(payload)
    assert "EAAG-super-secret-value" not in rendered
    assert "hunter2" not in rendered
    assert payload["access_token"] == "[REDACTED]"
    assert payload["config"]["password"] == "[REDACTED]"
    assert payload["config"]["host"] == "db", "non-sensitive context must survive"


def test_failed_login_logs_a_hash_not_the_email(caplog):
    with caplog.at_level(logging.WARNING, logger="growthos.events"):
        events.auth_failure(email="Victim@Example.com", reason="invalid_credentials")

    record = caplog.records[-1]
    assert record.email_hash
    assert "victim@example.com" not in json.dumps(record.__dict__, default=str).lower()


def test_auth_failure_hash_is_stable_for_correlation():
    import hashlib

    expected = hashlib.sha256(b"victim@example.com").hexdigest()[:16]
    assert events._hash_email("  Victim@Example.COM ") == expected


# --------------------------------------------------------------------------
# Correlation
# --------------------------------------------------------------------------


def test_context_variables_are_attached_to_every_record():
    bind_request_context(request_id="req-123", organization_id="org-1", user_id="user-1")
    payload = _emit("growthos.test", logging.INFO, "with context")
    assert payload["request_id"] == "req-123"
    assert payload["organization_id"] == "org-1"
    assert payload["user_id"] == "user-1"


@pytest.mark.asyncio
async def test_response_carries_a_request_id():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get("/health")
    assert response.headers.get("X-Request-ID")


@pytest.mark.asyncio
async def test_inbound_request_id_is_reused_so_traces_join_up():
    supplied = "trace-from-the-edge-proxy"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get("/health", headers={"X-Request-ID": supplied})
    assert response.headers["X-Request-ID"] == supplied


@pytest.mark.asyncio
async def test_absurdly_long_request_id_is_truncated():
    """A client must not be able to write unbounded data into every log line."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get("/health", headers={"X-Request-ID": "x" * 5000})
    assert len(response.headers["X-Request-ID"]) <= 64


@pytest.mark.asyncio
async def test_requests_get_distinct_ids():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        first = (await http.get("/health")).headers["X-Request-ID"]
        second = (await http.get("/health")).headers["X-Request-ID"]
    assert first != second


@pytest.mark.asyncio
async def test_request_log_line_records_method_path_status_and_duration(caplog):
    transport = ASGITransport(app=app)
    with caplog.at_level(logging.INFO, logger="growthos.request"):
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            await http.get("/api/v1/jobs")

    record = next(r for r in caplog.records if getattr(r, "path", None) == "/api/v1/jobs")
    assert record.http_method == "GET"
    assert record.status_code in {401, 403}
    assert record.duration_ms >= 0
    assert record.request_id


# --------------------------------------------------------------------------
# Events reach the log with tenant attribution
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authentication_events_are_logged(caplog):
    suffix = uuid.uuid4().hex[:8]
    email = f"logtest-{suffix}@example.com"
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"Log {suffix}", slug=f"log-{suffix}", demo_mode=False)
        user = User(email=email, hashed_password=hash_password(PASSWORD), full_name="Log")
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        await db.commit()

    transport = ASGITransport(app=app)
    with caplog.at_level(logging.INFO, logger="growthos.events"):
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            await http.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})
            await http.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})

    seen = {getattr(r, "event", None) for r in caplog.records}
    assert "auth.failure" in seen
    assert "auth.success" in seen


@pytest.mark.asyncio
async def test_authorization_denials_are_logged_with_role_and_permission(caplog):
    suffix = uuid.uuid4().hex[:8]
    email = f"viewer-{suffix}@example.com"
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"V {suffix}", slug=f"vl-{suffix}", demo_mode=False)
        user = User(email=email, hashed_password=hash_password(PASSWORD), full_name="Viewer")
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.viewer))
        await db.commit()

    transport = ASGITransport(app=app)
    with caplog.at_level(logging.WARNING, logger="growthos.events"):
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            token = (await http.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})).json()
            await http.post(
                "/api/v1/clients",
                headers={"Authorization": f"Bearer {token['access_token']}"},
                json={"business_name": "Nope", "industry": "saas"},
            )

    denial = next(r for r in caplog.records if getattr(r, "event", None) == "authz.denied")
    assert denial.role == "viewer"
    assert denial.permission


def test_rate_limit_rejections_are_logged_without_the_key(caplog):
    import asyncio

    from app.security import rate_limit as rl

    rl.set_rate_limit_backend(rl.InMemoryRateLimitBackend())
    policy = rl.RateLimitPolicy("unit", limit=1, window_seconds=60)

    async def run():
        await rl.enforce("ip:203.0.113.7", policy, scope="auth_ip")
        with pytest.raises(Exception):
            await rl.enforce("ip:203.0.113.7", policy, scope="auth_ip")

    with caplog.at_level(logging.WARNING, logger="app.security.rate_limit"):
        asyncio.run(run())

    record = caplog.records[-1]
    assert record.scope == "auth_ip"
    assert "203.0.113.7" not in json.dumps(record.__dict__, default=str)
    rl.set_rate_limit_backend(None)
