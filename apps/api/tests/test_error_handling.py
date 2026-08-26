"""P1-5 — one error shape, no internals across the boundary."""

from __future__ import annotations

import logging

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError, OperationalError

from app.ai.providers.base import AIGenerationError
from app.ai.providers.factory import AIProviderConfigurationError
from app.core.errors import (
    INTERNAL_MESSAGE,
    AppError,
    ProviderError,
    register_exception_handlers,
)
from app.main import app as real_app
from app.observability.middleware import RequestContextMiddleware
from app.storage.object_storage import StorageConfigurationError, StorageUnavailableError

SECRET = "postgresql://admin:sup3rsecret@prod-db.internal:5432/growthos"


class Payload(BaseModel):
    email: str
    password: str
    age: int


def build_app() -> FastAPI:
    """A miniature app with one route per failure class."""
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    router = APIRouter()

    @router.get("/boom")
    async def boom():
        raise RuntimeError(f"connection to {SECRET} failed")

    @router.get("/http-error")
    async def http_error():
        raise HTTPException(status_code=404, detail="Client not found")

    @router.get("/coded-http-error")
    async def coded_http_error():
        raise HTTPException(status_code=400, detail="BUDGET_REQUIRED: A budget must be set first.")

    @router.get("/app-error")
    async def app_error():
        raise AppError("Quota exhausted.", code="QUOTA_EXCEEDED", status_code=402)

    @router.get("/provider-error")
    async def provider_error():
        raise ProviderError("Meta rejected the request.")

    @router.get("/ai-error")
    async def ai_error():
        raise AIGenerationError(
            "openai returned 500: internal model id gpt-x-secret", provider="openai"
        )

    @router.get("/ai-config-error")
    async def ai_config_error():
        raise AIProviderConfigurationError("OPENAI_API_KEY is not set")

    @router.get("/storage-down")
    async def storage_down():
        raise StorageUnavailableError(f"S3 unreachable at {SECRET}")

    @router.get("/storage-misconfigured")
    async def storage_misconfigured():
        raise StorageConfigurationError("S3_BUCKET is empty")

    @router.get("/db-conflict")
    async def db_conflict():
        raise IntegrityError(
            "INSERT INTO users (email) VALUES ('victim@example.com')",
            {"email": "victim@example.com"},
            Exception("duplicate key value violates unique constraint"),
        )

    @router.get("/db-down")
    async def db_down():
        raise OperationalError("SELECT 1", {}, Exception(f"could not connect to {SECRET}"))

    @router.post("/validated")
    async def validated(payload: Payload):
        return {"ok": payload.email}

    app.include_router(router)
    register_exception_handlers(app)
    return app


@pytest.fixture
def client_app():
    return build_app()


async def call(app: FastAPI, method: str, path: str, **kwargs):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        return await http.request(method, path, **kwargs)


# --------------------------------------------------------------------------
# The envelope
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/boom",
        "/http-error",
        "/app-error",
        "/ai-error",
        "/storage-down",
        "/db-conflict",
        "/db-down",
    ],
)
async def test_every_failure_uses_the_same_envelope(client_app, path):
    body = (await call(client_app, "GET", path)).json()
    assert set(body["error"]) >= {"code", "message", "request_id"}
    assert isinstance(body["error"]["code"], str) and body["error"]["code"].isupper()
    assert body["error"]["message"]
    assert body["error"]["request_id"]


@pytest.mark.asyncio
async def test_error_request_id_matches_the_response_header(client_app):
    response = await call(client_app, "GET", "/boom")
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


@pytest.mark.asyncio
async def test_supplied_request_id_flows_into_the_error_body(client_app):
    response = await call(
        client_app, "GET", "/boom", headers={"X-Request-ID": "support-ticket-4417"}
    )
    assert response.json()["error"]["request_id"] == "support-ticket-4417"


@pytest.mark.asyncio
async def test_legacy_detail_mirror_is_kept_for_existing_clients(client_app):
    body = (await call(client_app, "GET", "/http-error")).json()
    assert body["detail"] == body["error"]["message"] == "Client not found"


# --------------------------------------------------------------------------
# Nothing internal crosses the boundary
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unhandled_exception_returns_a_generic_500(client_app):
    response = await call(client_app, "GET", "/boom")
    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "INTERNAL_ERROR",
        "message": INTERNAL_MESSAGE,
        "request_id": response.headers["X-Request-ID"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/boom", "/storage-down", "/db-down"])
async def test_credentials_in_an_exception_never_reach_the_response(client_app, path):
    raw = (await call(client_app, "GET", path)).text
    assert SECRET not in raw
    assert "sup3rsecret" not in raw
    assert "prod-db.internal" not in raw


@pytest.mark.asyncio
async def test_no_response_contains_a_traceback(client_app):
    raw = (await call(client_app, "GET", "/boom")).text
    assert "Traceback" not in raw
    assert "RuntimeError" not in raw
    assert ".py" not in raw


@pytest.mark.asyncio
async def test_database_error_does_not_echo_sql_or_row_values(client_app):
    raw = (await call(client_app, "GET", "/db-conflict")).text
    assert "INSERT INTO" not in raw
    assert "victim@example.com" not in raw
    assert "unique constraint" not in raw


@pytest.mark.asyncio
async def test_validation_error_does_not_echo_the_submitted_password(client_app):
    """Pydantic puts the rejected input in every error; it must be stripped."""
    response = await call(
        client_app,
        "POST",
        "/validated",
        json={"email": "a@b.com", "password": "hunter2-the-real-password", "age": "not a number"},
    )
    assert response.status_code == 422
    raw = response.text
    assert "hunter2-the-real-password" not in raw
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_validation_error_still_names_the_offending_field(client_app):
    response = await call(
        client_app, "POST", "/validated", json={"email": "a@b.com", "password": "x"}
    )
    fields = {item["field"] for item in response.json()["error"]["fields"]}
    assert "age" in fields


# --------------------------------------------------------------------------
# Detail goes to the log instead
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_traceback_is_logged_with_the_request_id(client_app, caplog):
    with caplog.at_level(logging.ERROR, logger="growthos.error"):
        response = await call(client_app, "GET", "/boom")

    record = next(r for r in caplog.records if getattr(r, "event", None) == "error.unhandled")
    assert record.request_id == response.json()["error"]["request_id"]
    assert record.exc_info, "the stack trace must be captured server-side"
    assert SECRET in logging.Formatter().formatException(record.exc_info)


@pytest.mark.asyncio
async def test_database_failures_emit_a_database_event(client_app, caplog):
    with caplog.at_level(logging.ERROR, logger="growthos.events"):
        await call(client_app, "GET", "/db-down")
    assert any(getattr(r, "event", None) == "database.error" for r in caplog.records)


# --------------------------------------------------------------------------
# Codes are specific enough for a UI to branch on
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,status,code",
    [
        ("/http-error", 404, "NOT_FOUND"),
        ("/coded-http-error", 400, "BUDGET_REQUIRED"),
        ("/app-error", 402, "QUOTA_EXCEEDED"),
        ("/provider-error", 502, "PROVIDER_ERROR"),
        ("/ai-error", 502, "AI_GENERATION_FAILED"),
        ("/ai-config-error", 503, "CONFIGURATION_ERROR"),
        ("/storage-down", 503, "STORAGE_UNAVAILABLE"),
        ("/storage-misconfigured", 503, "STORAGE_CONFIGURATION_ERROR"),
        ("/db-conflict", 409, "CONFLICT"),
        ("/db-down", 503, "DATABASE_UNAVAILABLE"),
        ("/boom", 500, "INTERNAL_ERROR"),
    ],
)
async def test_failure_classes_map_to_distinct_codes(client_app, path, status, code):
    response = await call(client_app, "GET", path)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


@pytest.mark.asyncio
async def test_embedded_code_is_lifted_out_of_the_message(client_app):
    body = (await call(client_app, "GET", "/coded-http-error")).json()["error"]
    assert body["code"] == "BUDGET_REQUIRED"
    assert body["message"] == "A budget must be set first."


@pytest.mark.asyncio
async def test_storage_outage_is_503_not_404(client_app):
    """A transient outage reported as 404 would look like a deleted asset."""
    assert (await call(client_app, "GET", "/storage-down")).status_code == 503


@pytest.mark.asyncio
async def test_ai_failure_names_the_provider_without_leaking_its_error(client_app):
    body = (await call(client_app, "GET", "/ai-error")).json()["error"]
    assert body["provider"] == "openai"
    assert "gpt-x-secret" not in str(body)


# --------------------------------------------------------------------------
# The real application
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_app_unauthenticated_error_is_structured():
    response = await call(real_app, "GET", "/api/v1/clients")
    assert response.status_code in {401, 403}
    assert response.json()["error"]["code"] in {"UNAUTHENTICATED", "PERMISSION_DENIED"}
    assert response.json()["error"]["request_id"]


@pytest.mark.asyncio
async def test_real_app_unknown_route_is_structured():
    body = (await call(real_app, "GET", "/api/v1/does-not-exist")).json()
    assert body["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_rate_limit_error_keeps_its_retry_after_header():
    """The envelope must not swallow headers the client needs."""
    from app.security import rate_limit as rl

    rl.set_rate_limit_backend(rl.InMemoryRateLimitBackend())
    try:
        responses = []
        transport = ASGITransport(app=real_app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            for _ in range(40):
                responses.append(
                    await http.post(
                        "/api/v1/auth/login",
                        json={"email": "nobody@example.com", "password": "wrong"},
                    )
                )
        throttled = next(r for r in responses if r.status_code == 429)
        assert throttled.headers["Retry-After"].isdigit()
        assert throttled.json()["error"]["code"] == "RATE_LIMITED"
    finally:
        rl.set_rate_limit_backend(None)
