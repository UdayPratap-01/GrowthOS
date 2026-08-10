"""
Fail-fast configuration validation.

The application must never boot into a state where it can silently:
  - sign tokens with a publicly known key,
  - simulate work and report it as real,
  - or fabricate AI output.

`validate_configuration()` runs at startup and raises before the app serves traffic.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings

MIN_SECRET_LENGTH = 32
MIN_DISTINCT_CHARS = 8

# Substrings that indicate a shipped placeholder rather than a real secret.
PLACEHOLDER_MARKERS = (
    "change-me",
    "change_me",
    "changeme",
    "dev-secret",
    "docker-dev-secret",
    "your-secret",
    "your_secret",
    "placeholder",
    "replace-me",
    "example",
    "test-key",
    "insecure",
)


class ConfigurationError(RuntimeError):
    """Raised when the runtime configuration is unsafe for the target environment."""


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _is_weak(value: str) -> bool:
    """Reject short or low-entropy secrets (e.g. 'aaaa...')."""
    stripped = value.strip()
    if len(stripped) < MIN_SECRET_LENGTH:
        return True
    return len(set(stripped)) < MIN_DISTINCT_CHARS


def _check_secret(name: str, value: str, errors: list[str]) -> None:
    if not value or not value.strip():
        errors.append(f"{name} is required and must be set to a strong random value.")
        return
    if _looks_like_placeholder(value):
        errors.append(
            f"{name} is set to a known placeholder value. "
            f"Generate a real secret (e.g. `openssl rand -hex 32`)."
        )
        return
    if _is_weak(value):
        errors.append(
            f"{name} is too weak: it must be at least {MIN_SECRET_LENGTH} characters "
            f"with at least {MIN_DISTINCT_CHARS} distinct characters."
        )


def _check_storage(settings: Settings, errors: list[str]) -> None:
    """Production must persist assets somewhere that survives a redeploy."""
    from app.storage.object_storage import LOCAL_ALIASES, S3_ALIASES

    backend = (settings.storage_backend or "local").strip().lower()
    if backend in LOCAL_ALIASES:
        errors.append(
            "STORAGE_BACKEND=local is not allowed in production. Container "
            "filesystems are ephemeral, so every generated image, video and report "
            "would disappear on the next deploy. Set STORAGE_BACKEND=s3."
        )
        return
    if backend not in S3_ALIASES:
        errors.append(
            f"Unknown STORAGE_BACKEND={backend!r}. Supported: "
            f"{', '.join(sorted(LOCAL_ALIASES | S3_ALIASES))}."
        )
        return
    if not (settings.s3_bucket or "").strip():
        errors.append("S3_BUCKET is required when STORAGE_BACKEND=s3.")
    has_key = bool((settings.s3_access_key_id or "").strip())
    has_secret = bool((settings.s3_secret_access_key or "").strip())
    if has_key != has_secret:
        errors.append(
            "S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be set together "
            "(or omit both to use an instance role / IRSA)."
        )


def _check_proxy_trust(settings: Settings, errors: list[str]) -> None:
    """
    Production must state whether it sits behind a proxy.

    Left ambiguous, the app either trusts a forged X-Forwarded-For (bypassing
    rate limits) or keys every request on a load-balancer address (locking
    everyone out together). Neither is something to discover in production, so
    the choice has to be written down.
    """
    import ipaddress

    raw = (settings.trusted_proxy_ips or "").strip()
    if not raw:
        errors.append(
            "TRUSTED_PROXY_IPS must be set in production. Use the proxy/load-balancer "
            "addresses (comma-separated IPs or CIDRs), or the literal 'none' if the "
            "application is exposed directly. Unset is ambiguous: X-Forwarded-For "
            "would be ignored and every request keyed on the same address."
        )
        return
    if raw == "*":
        errors.append(
            "TRUSTED_PROXY_IPS='*' is not allowed in production: it trusts an "
            "X-Forwarded-For header from any client, which makes IP rate limits "
            "trivially bypassable."
        )
        return
    if raw.lower() in {"none", "off", "false", "disabled"}:
        return
    for part in raw.split(","):
        candidate = part.strip()
        if not candidate:
            continue
        try:
            ipaddress.ip_network(candidate, strict=False)
        except ValueError:
            errors.append(
                f"TRUSTED_PROXY_IPS entry {candidate!r} is not an IP address or CIDR block."
            )


def validate_configuration(settings: Settings | None = None) -> None:
    """
    Validate configuration for the declared ENVIRONMENT.

    Development is permissive. Staging and production require real secrets.
    Production additionally forbids demo mode, mock AI, SQLite, local object
    storage and wildcard CORS.
    """
    settings = settings or get_settings()
    env = settings.env
    errors: list[str] = []

    if env not in {"development", "staging", "production"}:
        raise ConfigurationError(
            f"ENVIRONMENT must be one of development|staging|production, got {settings.environment!r}."
        )

    # --- Secrets: required outside development -------------------------------
    if env != "development":
        _check_secret("SECRET_KEY", settings.secret_key, errors)
        _check_secret("ENCRYPTION_KEY", settings.encryption_key, errors)

    if env != "production":
        _raise_if_any(errors, env)
        return

    # --- Production-only safety ---------------------------------------------
    if settings.demo_mode:
        errors.append(
            "DEMO_MODE must be false in production. Demo mode simulates executions "
            "and would report simulated work as success."
        )

    provider = (settings.ai_provider or "").strip().lower()
    if provider in {"", "mock", "fake", "stub"}:
        errors.append(
            "AI_PROVIDER must be a real provider in production (openai|anthropic). "
            "The mock provider returns fabricated marketing analysis."
        )
    elif provider == "openai" and not (settings.openai_api_key or "").strip():
        errors.append("OPENAI_API_KEY is required when AI_PROVIDER=openai.")
    elif provider == "anthropic" and not (settings.anthropic_api_key or "").strip():
        errors.append("ANTHROPIC_API_KEY is required when AI_PROVIDER=anthropic.")

    if settings.database_url.strip().lower().startswith("sqlite"):
        errors.append("DATABASE_URL must point at PostgreSQL in production, not SQLite.")

    if not (settings.redis_url or "").strip():
        errors.append(
            "REDIS_URL is required in production. Without a shared rate-limit backend, "
            "per-process counters give an attacker N times the budget on N instances."
        )

    if settings.inline_job_execution:
        errors.append(
            "INLINE_JOB_EXECUTION must not be enabled in production. Long media and "
            "report generation would run inside the HTTP request and be lost on "
            "restart. Run `python -m app.worker` as a separate process."
        )

    _check_storage(settings, errors)

    if settings.db_auto_create:
        errors.append(
            "DB_AUTO_CREATE must not be enabled in production. "
            "Schema changes are applied with `alembic upgrade head`."
        )

    if not (settings.metrics_token or "").strip():
        errors.append(
            "METRICS_TOKEN is required in production. /metrics sits outside the "
            "authenticated API and exposes traffic shape, error ratios and provider "
            "names; without a token it would be readable by anyone who can reach the pod."
        )

    _check_proxy_trust(settings, errors)

    if "*" in settings.cors_origins:
        errors.append(
            "API_CORS_ORIGINS must list explicit origins in production. "
            "A wildcard with credentials enabled is unsafe."
        )

    _raise_if_any(errors, env)


def _raise_if_any(errors: list[str], env: str) -> None:
    if not errors:
        return
    bullets = "\n".join(f"  - {e}" for e in errors)
    raise ConfigurationError(
        f"Refusing to start: invalid configuration for ENVIRONMENT={env}.\n{bullets}"
    )
