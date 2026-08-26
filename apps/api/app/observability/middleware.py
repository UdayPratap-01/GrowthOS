"""
Request correlation and access logging.

Every request gets an ID — reused from an inbound `X-Request-ID` when a proxy or
the frontend supplies one, otherwise generated — which is bound to a ContextVar
for the duration of the request and echoed in the response header. Any log line
written while handling the request carries it, so a user reporting an error can
quote the ID from the error response and it will find every related line,
including the stack trace that was deliberately not shown to them.
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.services.usage_service import flush_usage, start_usage_buffer

from app.observability.logging import (
    bind_request_context,
    clear_request_context,
    organization_id_var,
    request_id_var,
)

logger = logging.getLogger("growthos.request")

REQUEST_ID_HEADER = "X-Request-ID"

#: Paths that would otherwise flood the logs with no diagnostic value.
QUIET_PATHS = frozenset({"/health", "/health/live", "/health/ready", "/metrics"})


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        # Cap the length so a client cannot use the header to write unbounded
        # attacker-controlled data into every log line.
        request_id = (incoming or "").strip()[:64] or uuid.uuid4().hex
        bind_request_context(request_id=request_id)
        request.state.request_id = request_id

        # Usage recorded while serving this request is collected and written
        # once, after the response, rather than opening a connection mid-request.
        start_usage_buffer()

        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            # The exception handler builds the response; log it here so the
            # duration and route are captured even for an unhandled error.
            logger.exception(
                "Request failed",
                extra={
                    "http_method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            await flush_usage()
            clear_request_context()
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id

        if request.url.path not in QUIET_PATHS:
            level = logging.WARNING if status_code >= 400 else logging.INFO
            logger.log(
                level,
                "%s %s -> %s",
                request.method,
                request.url.path,
                status_code,
                extra={
                    "http_method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "client_ip": request.client.host if request.client else None,
                    # Set by get_current_auth once the caller is identified.
                    "org": organization_id_var.get(),
                },
            )

        from app.observability.metrics import record_request

        record_request(
            method=request.method,
            path=request.scope.get("route").path if request.scope.get("route") else request.url.path,
            status_code=status_code,
            duration_ms=duration_ms,
        )

        await flush_usage()
        clear_request_context()
        return response


def current_request_id() -> str | None:
    return request_id_var.get()
