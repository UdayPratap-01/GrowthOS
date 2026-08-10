"""
Global error handling.

Every failure leaves the API in one shape:

    {"error": {"code": "AI_GENERATION_FAILED",
               "message": "Unable to generate the requested creative.",
               "request_id": "…"}}

Two rules drive the design.

**Nothing internal crosses the boundary.** An unhandled exception becomes a
generic 500 with a fixed message. The exception type, its arguments and the
traceback go to the log, correlated by the same `request_id` the caller sees, so
support can find the detail without the caller ever being shown a database DSN
or a file path.

**The code is the contract.** `message` is for a human and may be reworded;
`code` is what the frontend branches on. Handlers below map each known failure
class to a stable code rather than letting the HTTP status carry all the meaning,
because "503" tells the UI nothing about whether to offer a retry.

A top-level `detail` mirror of the message is included for backwards
compatibility with clients written against FastAPI's default shape. New code
should read `error`.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_402_PAYMENT_REQUIRED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_405_METHOD_NOT_ALLOWED,
    HTTP_409_CONFLICT,
    HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    HTTP_422_UNPROCESSABLE_ENTITY,
    HTTP_429_TOO_MANY_REQUESTS,
    HTTP_500_INTERNAL_SERVER_ERROR,
    HTTP_502_BAD_GATEWAY,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from app.observability.logging import bind_request_context, get_request_id

logger = logging.getLogger("growthos.error")

#: Shown for any exception we did not anticipate. Deliberately says nothing.
INTERNAL_MESSAGE = "An unexpected error occurred. Quote the request ID if you contact support."

_STATUS_CODES = {
    HTTP_400_BAD_REQUEST: "BAD_REQUEST",
    HTTP_401_UNAUTHORIZED: "UNAUTHENTICATED",
    # The caller is authenticated and permitted; their plan does not cover it.
    HTTP_402_PAYMENT_REQUIRED: "QUOTA_EXCEEDED",
    HTTP_403_FORBIDDEN: "PERMISSION_DENIED",
    HTTP_404_NOT_FOUND: "NOT_FOUND",
    HTTP_405_METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
    HTTP_409_CONFLICT: "CONFLICT",
    HTTP_413_REQUEST_ENTITY_TOO_LARGE: "PAYLOAD_TOO_LARGE",
    HTTP_422_UNPROCESSABLE_ENTITY: "VALIDATION_ERROR",
    HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMITED",
    HTTP_500_INTERNAL_SERVER_ERROR: "INTERNAL_ERROR",
    HTTP_502_BAD_GATEWAY: "UPSTREAM_ERROR",
    HTTP_503_SERVICE_UNAVAILABLE: "SERVICE_UNAVAILABLE",
}

#: Existing call sites raise `HTTPException(403, "PERMISSION_DENIED: …")`. Lift
#: that prefix into the code rather than rewriting every raise site.
_EMBEDDED_CODE = re.compile(r"^([A-Z][A-Z0-9_]{2,63}):\s*(.*)$", re.DOTALL)


class AppError(Exception):
    """Application failure with a stable code, safe to show to the caller."""

    status_code = HTTP_500_INTERNAL_SERVER_ERROR
    code = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details or {}


class ProviderError(AppError):
    """An upstream provider failed. Not the caller's fault, may be retryable."""

    status_code = HTTP_502_BAD_GATEWAY
    code = "PROVIDER_ERROR"


class JobError(AppError):
    status_code = HTTP_500_INTERNAL_SERVER_ERROR
    code = "JOB_FAILED"


def request_id_for(request: Request | None) -> str | None:
    """
    Prefer the id recorded on the request.

    An unhandled exception unwinds past the middleware that owns the ContextVar,
    so by the time the outermost handler runs the variable may already be reset.
    `request.state` survives that unwinding, and this is exactly the case where
    the caller most needs a usable id.
    """
    if request is not None:
        recorded = getattr(request.state, "request_id", None)
        if recorded:
            return str(recorded)
    return get_request_id()



def _restore_context(request: Request | None) -> None:
    """
    Re-bind the request id before logging.

    An unhandled exception unwinds past the middleware, which resets the
    ContextVar on its way out, so without this the traceback would be logged
    with no id — the one log line that most needs to be findable from the id
    the caller was given.
    """
    request_id = request_id_for(request)
    if request_id:
        bind_request_context(request_id=request_id)


def error_body(
    code: str, message: str, *, request: Request | None = None, **extra: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "error": {"code": code, "message": message, "request_id": request_id_for(request)}
    }
    if extra:
        body["error"].update({k: v for k, v in extra.items() if v is not None})
    # Compatibility mirror; see module docstring.
    body["detail"] = message
    return body


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    request: Request | None = None,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    combined = dict(headers or {})
    request_id = request_id_for(request)
    if request_id:
        # Set here as well as in the middleware: an error response can bypass it.
        combined.setdefault("X-Request-ID", request_id)
    return JSONResponse(
        status_code=status_code,
        content=error_body(code, message, request=request, **extra),
        headers=combined or None,
    )


def _safe_validation_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """
    Field names and messages only.

    Pydantic includes the rejected `input` in every error. Echoing it back would
    put a mistyped password or an API key into the response body and into any
    client-side error logging, so it is dropped.
    """
    cleaned: list[dict[str, Any]] = []
    for error in exc.errors():
        location = [str(part) for part in error.get("loc", ()) if part != "body"]
        cleaned.append(
            {
                "field": ".".join(location) or "body",
                "message": str(error.get("msg", "Invalid value")),
                "type": str(error.get("type", "value_error")),
            }
        )
    return cleaned


def register_exception_handlers(app: FastAPI) -> None:
    from app.ai.providers.base import AIGenerationError
    from app.ai.providers.factory import AIProviderConfigurationError
    from app.storage.object_storage import (
        StorageConfigurationError,
        StorageError,
        StorageUnavailableError,
    )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        _restore_context(request)
        return error_response(
            HTTP_422_UNPROCESSABLE_ENTITY,
            "VALIDATION_ERROR",
            "The request body failed validation.",
            request=request,
            fields=_safe_validation_errors(exc),
        )

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        _restore_context(request)
        if exc.status_code >= 500:
            logger.error(
                "Application error",
                exc_info=exc,
                extra={"event": "error.app", "error_code": exc.code},
            )
        return error_response(exc.status_code, exc.code, exc.message, request=request, **exc.details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        _restore_context(request)
        detail = exc.detail
        code = _STATUS_CODES.get(exc.status_code, "ERROR")

        if isinstance(detail, dict):
            code = str(detail.get("code", code))
            message = str(detail.get("message", detail.get("detail", "")))
        else:
            message = str(detail)
            embedded = _EMBEDDED_CODE.match(message)
            if embedded:
                code, message = embedded.group(1), embedded.group(2).strip() or embedded.group(1)

        headers = dict(getattr(exc, "headers", None) or {})
        if exc.status_code >= 500:
            logger.error(
                "Request failed", exc_info=exc, extra={"event": "error.http", "error_code": code}
            )
        return error_response(exc.status_code, code, message, request=request, headers=headers or None)

    @app.exception_handler(AIProviderConfigurationError)
    async def _ai_config(request: Request, exc: AIProviderConfigurationError) -> JSONResponse:
        _restore_context(request)
        logger.error(
            "AI provider misconfigured", exc_info=exc, extra={"event": "error.ai_configuration"}
        )
        return error_response(
            HTTP_503_SERVICE_UNAVAILABLE, "CONFIGURATION_ERROR", str(exc), request=request
        )

    @app.exception_handler(AIGenerationError)
    async def _ai_generation(request: Request, exc: AIGenerationError) -> JSONResponse:
        _restore_context(request)
        # Explicit failure. Never substitute fabricated content for a failed call.
        logger.error(
            "AI generation failed",
            exc_info=exc,
            extra={"event": "error.ai_generation", "provider": exc.provider},
        )
        return error_response(
            HTTP_502_BAD_GATEWAY,
            "AI_GENERATION_FAILED",
            "Unable to generate the requested content. The AI provider returned an error.",
            request=request,
            provider=exc.provider,
        )

    @app.exception_handler(StorageConfigurationError)
    async def _storage_config(request: Request, exc: StorageConfigurationError) -> JSONResponse:
        _restore_context(request)
        logger.error(
            "Storage misconfigured", exc_info=exc, extra={"event": "error.storage_configuration"}
        )
        return error_response(
            HTTP_503_SERVICE_UNAVAILABLE,
            "STORAGE_CONFIGURATION_ERROR",
            "Object storage is not correctly configured.",
            request=request,
        )

    @app.exception_handler(StorageUnavailableError)
    async def _storage_unavailable(request: Request, exc: StorageUnavailableError) -> JSONResponse:
        _restore_context(request)
        # 503, never 404: a transient outage must not be reported as a deleted asset.
        logger.error(
            "Storage unavailable", exc_info=exc, extra={"event": "error.storage_unavailable"}
        )
        return error_response(
            HTTP_503_SERVICE_UNAVAILABLE,
            "STORAGE_UNAVAILABLE",
            "Object storage is temporarily unavailable. The asset was not lost.",
            request=request,
        )

    @app.exception_handler(StorageError)
    async def _storage(request: Request, exc: StorageError) -> JSONResponse:
        _restore_context(request)
        logger.error("Storage error", exc_info=exc, extra={"event": "error.storage"})
        return error_response(
            HTTP_503_SERVICE_UNAVAILABLE,
            "STORAGE_ERROR",
            "A storage operation failed.",
            request=request,
        )

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError) -> JSONResponse:
        _restore_context(request)
        # The driver message names columns, constraints and sometimes values.
        logger.warning(
            "Integrity constraint violated", exc_info=exc, extra={"event": "error.db_integrity"}
        )
        return error_response(
            HTTP_409_CONFLICT,
            "CONFLICT",
            "The request conflicts with existing data.",
            request=request,
        )

    @app.exception_handler(OperationalError)
    async def _operational(request: Request, exc: OperationalError) -> JSONResponse:
        _restore_context(request)
        _log_database_error(exc, "operational")
        return error_response(
            HTTP_503_SERVICE_UNAVAILABLE,
            "DATABASE_UNAVAILABLE",
            "The service is temporarily unable to reach its database.",
            request=request,
        )

    @app.exception_handler(DBAPIError)
    async def _dbapi(request: Request, exc: DBAPIError) -> JSONResponse:
        _restore_context(request)
        _log_database_error(exc, "dbapi")
        return error_response(
            HTTP_503_SERVICE_UNAVAILABLE if exc.connection_invalidated else HTTP_500_INTERNAL_SERVER_ERROR,
            "DATABASE_ERROR",
            "A database error prevented the request from completing.",
            request=request,
        )

    @app.exception_handler(SQLAlchemyError)
    async def _sqlalchemy(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        _restore_context(request)
        _log_database_error(exc, "sqlalchemy")
        return error_response(
            HTTP_500_INTERNAL_SERVER_ERROR,
            "DATABASE_ERROR",
            "A database error prevented the request from completing.",
            request=request,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        _restore_context(request)
        # The only place a traceback is produced, and it goes to the log alone.
        logger.exception(
            "Unhandled exception",
            extra={
                "event": "error.unhandled",
                "exception_type": type(exc).__name__,
                "path": request.url.path,
            },
        )
        return error_response(
            HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", INTERNAL_MESSAGE, request=request
        )


def _log_database_error(exc: Exception, kind: str) -> None:
    from app.observability import events, metrics

    logger.error("Database error", exc_info=exc, extra={"event": "error.database", "kind": kind})
    events.database_error(operation=kind, detail=type(exc).__name__)
    metrics.record_database_error(kind=kind)
