"""
P1-12 — production configuration is explicit and fails fast.

Guards against silent demo defaults, mock providers, local storage, and
missing shared infrastructure sneaking into a production boot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.startup_checks import ConfigurationError, validate_configuration

STRONG = "9f2c41b7ae0d63528c1fb47e0a95d3ce7b6108fa24d9e35c07bd18492af6c3e5"
STRONG2 = "c07bd18492af6c3e59f2c41b7ae0d63528c1fb47e0a95d3ce7b6108fa24d9e35"


def _prod(**overrides) -> Settings:
    base = dict(
        environment="production",
        secret_key=STRONG,
        encryption_key=STRONG2,
        demo_mode=False,
        ai_provider="openai",
        openai_api_key="sk-live-test",
        database_url="postgresql+asyncpg://u:p@db:5432/growthos",
        api_cors_origins="https://app.example.com",
        redis_url="redis://cache:6379/0",
        storage_backend="s3",
        s3_bucket="growthos-assets",
        metrics_token="metrics-token-not-a-placeholder-value",
        trusted_proxy_ips="10.0.0.0/8",
        inline_job_execution=False,
        db_auto_create=False,
        allow_demo_seed=False,
    )
    base.update(overrides)
    return Settings(**base)


def test_production_accepts_a_fully_specified_configuration():
    validate_configuration(_prod())


@pytest.mark.parametrize(
    "override,needle",
    [
        ({"secret_key": "dev-secret-change-me"}, "SECRET_KEY"),
        ({"demo_mode": True}, "DEMO_MODE"),
        ({"ai_provider": "mock"}, "AI_PROVIDER"),
        ({"database_url": "sqlite+aiosqlite:///./x.db"}, "DATABASE_URL"),
        ({"redis_url": ""}, "REDIS_URL"),
        ({"storage_backend": "local"}, "STORAGE_BACKEND"),
        ({"storage_backend": "s3", "s3_bucket": ""}, "S3_BUCKET"),
        ({"inline_job_execution": True}, "INLINE_JOB_EXECUTION"),
        ({"db_auto_create": True}, "DB_AUTO_CREATE"),
        ({"metrics_token": ""}, "METRICS_TOKEN"),
        ({"trusted_proxy_ips": ""}, "TRUSTED_PROXY_IPS"),
        ({"trusted_proxy_ips": "*"}, "TRUSTED_PROXY_IPS"),
        ({"trusted_proxy_ips": "not-an-ip"}, "TRUSTED_PROXY_IPS"),
        ({"api_cors_origins": "*"}, "CORS"),
    ],
)
def test_production_fails_fast_on_each_required_guard(override, needle):
    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(_prod(**override))
    assert needle in str(exc.value).upper() or needle in str(exc.value)


def test_development_allows_local_defaults():
    validate_configuration(
        Settings(
            environment="development",
            demo_mode=True,
            ai_provider="mock",
            storage_backend="local",
            redis_url="",
            metrics_token="",
            database_url="sqlite+aiosqlite:///./growthos.db",
        )
    )


def test_staging_requires_real_secrets_but_not_full_production_stack():
    # Staging must not boot on placeholder secrets…
    with pytest.raises(ConfigurationError):
        validate_configuration(
            Settings(
                environment="staging",
                secret_key="dev-secret-change-me",
                encryption_key="change-me-32-byte-fernet-compatible-key!!",
            )
        )
    # …but may still use local storage / no redis while hardening.
    validate_configuration(
        Settings(
            environment="staging",
            secret_key=STRONG,
            encryption_key=STRONG2,
            storage_backend="local",
            redis_url="",
            metrics_token="",
        )
    )


def test_env_example_documents_every_production_required_variable():
    """
    .env.example is the operator contract. If a production-required setting is
    missing from it, a deploy will fail at boot with no documentation.
    """
    example = (Path(__file__).resolve().parents[3] / ".env.example").read_text(encoding="utf-8")
    required = [
        "ENVIRONMENT",
        "SECRET_KEY",
        "ENCRYPTION_KEY",
        "DEMO_MODE",
        "DATABASE_URL",
        "REDIS_URL",
        "AI_PROVIDER",
        "STORAGE_BACKEND",
        "S3_BUCKET",
        "INLINE_JOB_EXECUTION",
        "METRICS_TOKEN",
        "TRUSTED_PROXY_IPS",
        "API_CORS_ORIGINS",
        "DB_AUTO_CREATE",
        "RATE_LIMIT_PER_MINUTE",
        "AUTH_RATE_LIMIT_PER_MINUTE",
        "LOG_FORMAT",
        "WORKER_POLL_INTERVAL_SECONDS",
    ]
    missing = [name for name in required if name not in example]
    assert not missing, f".env.example is missing: {missing}"


def test_env_example_ships_no_demo_credentials_or_live_secrets():
    example = (Path(__file__).resolve().parents[3] / ".env.example").read_text(encoding="utf-8")
    assert "demo1234" not in example
    # Placeholder markers are fine; real-looking keys are not.
    for line in example.splitlines():
        if line.startswith("SECRET_KEY=") and "CHANGE_ME" not in line and "openssl" not in line.lower():
            value = line.split("=", 1)[1].strip()
            assert not value or "CHANGE_ME" in value or "generate" in value.lower()


def test_no_demo_defaults_in_production_settings_object():
    s = Settings(
        _env_file=None,
        environment="production",
        secret_key=STRONG,
        encryption_key=STRONG2,
        openai_api_key="sk-x",
        ai_provider="openai",
        redis_url="redis://x",
        storage_backend="s3",
        s3_bucket="b",
        metrics_token="t",
        database_url="postgresql+asyncpg://u:p@db/db",
        api_cors_origins="https://app.example.com",
        demo_mode=False,
    )
    assert s.demo_mode is False
    assert s.ai_provider != "mock"
    assert s.storage_backend != "local"
    assert s.should_run_jobs_inline is False
    assert s.should_auto_create_tables is False
