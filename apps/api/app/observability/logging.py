"""
Structured logging.

Production emits one JSON object per line so a log aggregator can index fields
directly. Development emits a human-readable line, because JSON in a terminal is
unreadable and nobody greps their own laptop.

Correlation
-----------
`request_id` is stored in a `ContextVar`, so every log line emitted while
handling a request carries it without being threaded through call signatures.
The same variable is set by the worker per job, so job logs correlate too.

Redaction
---------
Passwords, tokens, API keys and authorization headers must never reach the logs.
`redact()` scrubs known-sensitive keys from structured payloads, and a filter
applies it to every record's extra fields. This is a safety net, not a licence
to pass secrets to the logger.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
organization_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "organization_id", default=None
)
user_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("user_id", default=None)

#: Substrings that mark a value as unloggable. Matched case-insensitively
#: against the key, so `META_APP_SECRET` and `refresh_token` are both caught.
SENSITIVE_KEY_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "auth_header",
    "credential",
    "private_key",
    "access_key",
    "session",
    "cookie",
    "signature",
)

REDACTED = "[REDACTED]"

#: Attributes present on every LogRecord; anything else is treated as structured
#: context supplied by the caller.
_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime", "taskName"}


def is_sensitive_key(key: str) -> bool:
    lowered = str(key).lower()
    return any(marker in lowered for marker in SENSITIVE_KEY_MARKERS)


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Recursively replace values whose key looks sensitive."""
    if _depth > 6:
        return value
    if isinstance(value, dict):
        return {
            k: (REDACTED if is_sensitive_key(k) else redact(v, _depth=_depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v, _depth=_depth + 1) for v in value]
    return value


class ContextFilter(logging.Filter):
    """Attach correlation identifiers and redact structured extras."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.organization_id = organization_id_var.get()
        record.user_id = user_id_var.get()
        for key in list(record.__dict__):
            if key in _STANDARD_ATTRS or key in {"request_id", "organization_id", "user_id"}:
                continue
            if is_sensitive_key(key):
                record.__dict__[key] = REDACTED
            else:
                record.__dict__[key] = redact(record.__dict__[key])
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, *, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": self.service,
            "message": record.getMessage(),
        }
        for key in ("request_id", "organization_id", "user_id"):
            value = getattr(record, key, None)
            if value:
                payload[key] = value
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key in payload:
                continue
            payload[key] = value
        if record.exc_info:
            # Stack traces belong in the logs, never in an API response.
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class HumanFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-8s %(name)s: %(message)s", "%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        request_id = getattr(record, "request_id", None)
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _STANDARD_ATTRS
            and k not in {"request_id", "organization_id", "user_id"}
            and v is not None
        }
        parts = [base]
        if request_id:
            parts.append(f"[req {request_id}]")
        if extras:
            parts.append(" ".join(f"{k}={v}" for k, v in extras.items()))
        return " ".join(parts)


_configured = False


def configure_logging(*, service: str = "growthos-api", force: bool = False) -> None:
    """Install handlers on the root logger. Idempotent."""
    global _configured
    if _configured and not force:
        return

    from app.core.config import get_settings

    settings = get_settings()
    level = logging.getLevelName((settings.log_level or "INFO").upper())
    if not isinstance(level, int):
        level = logging.INFO

    fmt = (settings.log_format or "").strip().lower()
    if fmt not in {"json", "text"}:
        fmt = "text" if settings.is_development else "json"

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service=service) if fmt == "json" else HumanFormatter())
    handler.addFilter(ContextFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # Uvicorn installs its own handlers; route them through ours so every line
    # in production is JSON and carries the request id.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        uv.handlers = []
        uv.propagate = True

    _configured = True


def bind_request_context(
    *, request_id: str | None = None, organization_id: str | None = None, user_id: str | None = None
) -> None:
    if request_id is not None:
        request_id_var.set(request_id)
    if organization_id is not None:
        organization_id_var.set(organization_id)
    if user_id is not None:
        user_id_var.set(user_id)


def clear_request_context() -> None:
    request_id_var.set(None)
    organization_id_var.set(None)
    user_id_var.set(None)


def get_request_id() -> str | None:
    return request_id_var.get()
