"""P0 hardening guards: demo seeding, secret validation, demo mode, AI provider selection."""

from __future__ import annotations

import pytest

from app.core.config import Settings, get_settings


# Realistic high-entropy values, as produced by `openssl rand -hex 32`.
STRONG_SECRET = "9f2c41b7ae0d63528c1fb47e0a95d3ce7b6108fa24d9e35c07bd18492af6c3e5"
STRONG_ENCRYPTION_KEY = "c07bd18492af6c3e59f2c41b7ae0d63528c1fb47e0a95d3ce7b6108fa24d9e35"


def _settings(**overrides) -> Settings:
    base = {
        "environment": "production",
        "secret_key": STRONG_SECRET,
        "encryption_key": STRONG_ENCRYPTION_KEY,
        "demo_mode": False,
        "ai_provider": "openai",
        "openai_api_key": "sk-test-key",
        "storage_backend": "s3",
        "database_url": "postgresql+asyncpg://u:p@db:5432/growthos",
        "api_cors_origins": "https://app.example.com",
        "redis_url": "redis://cache:6379/0",
        "s3_bucket": "growthos-assets",
        "metrics_token": "test-metrics-token-not-a-placeholder",
        "trusted_proxy_ips": "none",
    }
    base.update(overrides)
    return Settings(**base)


# --------------------------------------------------------------------------
# P0-1 — demo seeding must never run in production
# --------------------------------------------------------------------------


def test_seed_blocked_in_production(monkeypatch):
    from app.demo import seed as seed_module

    monkeypatch.setattr(seed_module, "get_settings", lambda: _settings(allow_demo_seed=True))
    with pytest.raises(seed_module.SeedBlockedError) as exc:
        seed_module.assert_seeding_allowed()
    assert "production" in str(exc.value).lower()


def test_seed_blocked_when_flag_disabled(monkeypatch):
    from app.demo import seed as seed_module

    monkeypatch.setattr(
        seed_module, "get_settings", lambda: _settings(environment="development", allow_demo_seed=False)
    )
    with pytest.raises(seed_module.SeedBlockedError) as exc:
        seed_module.assert_seeding_allowed()
    assert "ALLOW_DEMO_SEED" in str(exc.value)


def test_seed_allowed_in_development(monkeypatch):
    from app.demo import seed as seed_module

    monkeypatch.setattr(
        seed_module, "get_settings", lambda: _settings(environment="development", allow_demo_seed=True)
    )
    seed_module.assert_seeding_allowed()  # must not raise


def test_dockerfile_does_not_seed_on_startup():
    from pathlib import Path

    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    content = dockerfile.read_text(encoding="utf-8")
    cmd_lines = [ln for ln in content.splitlines() if ln.startswith("CMD")]
    assert cmd_lines, "Dockerfile must define a CMD"
    assert not any("app.demo.seed" in ln for ln in cmd_lines), "Container startup must not seed demo data"
    assert "USER growthos" in content, "API container must not run as root"


# --------------------------------------------------------------------------
# P0-2 — secret validation
# --------------------------------------------------------------------------


def test_production_rejects_placeholder_secret_key():
    from app.core.startup_checks import ConfigurationError, validate_configuration

    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(_settings(secret_key="dev-secret-change-me"))
    assert "SECRET_KEY" in str(exc.value)


def test_production_rejects_empty_secret_key():
    from app.core.startup_checks import ConfigurationError, validate_configuration

    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(_settings(secret_key=""))
    assert "SECRET_KEY" in str(exc.value)


def test_production_rejects_short_secret_key():
    from app.core.startup_checks import ConfigurationError, validate_configuration

    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(_settings(secret_key="short"))
    assert "SECRET_KEY" in str(exc.value)


def test_production_rejects_low_entropy_secret_key():
    """A long but repetitive string is not a real secret."""
    from app.core.startup_checks import ConfigurationError, validate_configuration

    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(_settings(secret_key="a" * 64))
    assert "SECRET_KEY" in str(exc.value)


def test_production_rejects_placeholder_encryption_key():
    from app.core.startup_checks import ConfigurationError, validate_configuration

    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(_settings(encryption_key="change-me-32-byte-fernet-compatible-key!!"))
    assert "ENCRYPTION_KEY" in str(exc.value)


def test_production_accepts_strong_configuration():
    from app.core.startup_checks import validate_configuration

    validate_configuration(_settings())  # must not raise


def test_development_allows_placeholder_secrets():
    from app.core.startup_checks import validate_configuration

    validate_configuration(
        _settings(
            environment="development",
            secret_key="dev-secret-change-me",
            encryption_key="change-me-32-byte-fernet-compatible-key!!",
            demo_mode=True,
            ai_provider="mock",
            storage_backend="local",
            database_url="sqlite+aiosqlite:///./growthos.db",
        )
    )


# --------------------------------------------------------------------------
# P0-3 — DEMO_MODE safety
# --------------------------------------------------------------------------


def test_demo_mode_defaults_to_false():
    """Demo mode must be opt-in. Asserted on the declared default so a local
    .env cannot mask a regression in the shipped value."""
    assert Settings.model_fields["demo_mode"].default is False


def test_production_rejects_demo_mode_true():
    from app.core.startup_checks import ConfigurationError, validate_configuration

    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(_settings(demo_mode=True))
    assert "DEMO_MODE" in str(exc.value)


def test_production_rejects_wildcard_cors():
    from app.core.startup_checks import ConfigurationError, validate_configuration

    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(_settings(api_cors_origins="*"))
    assert "CORS" in str(exc.value).upper()


def test_production_rejects_sqlite():
    from app.core.startup_checks import ConfigurationError, validate_configuration

    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(_settings(database_url="sqlite+aiosqlite:///./growthos.db"))
    assert "DATABASE_URL" in str(exc.value)


# --------------------------------------------------------------------------
# P0-5 — migrations, not create_all, in production
# --------------------------------------------------------------------------


def test_production_never_auto_creates_tables():
    # Even if explicitly enabled, production must not run metadata.create_all.
    assert _settings(db_auto_create=True).should_auto_create_tables is False
    assert _settings(db_auto_create=None).should_auto_create_tables is False


def test_production_rejects_explicit_db_auto_create():
    from app.core.startup_checks import ConfigurationError, validate_configuration

    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(_settings(db_auto_create=True))
    assert "DB_AUTO_CREATE" in str(exc.value)


def test_development_auto_creates_by_default():
    assert _settings(environment="development", db_auto_create=None).should_auto_create_tables is True


def test_staging_requires_explicit_opt_in_for_create_all():
    assert _settings(environment="staging", db_auto_create=None).should_auto_create_tables is False


def test_initial_migration_exists_and_is_reversible():
    from pathlib import Path

    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    revisions = sorted(p for p in versions.glob("*.py") if not p.name.startswith("_"))
    assert revisions, "An initial Alembic migration must be committed"
    for revision in revisions:
        source = revision.read_text(encoding="utf-8")
        assert "def upgrade()" in source
        assert "def downgrade()" in source
        body = source.split("def downgrade()", 1)[1]
        assert "pass" not in body.split("\n")[1:3], f"{revision.name} has an empty downgrade"


def test_execution_modes_are_distinct():
    from app.core.mode import ExecutionMode

    assert ExecutionMode.demo_data.value == "DEMO_DATA"
    assert ExecutionMode.demo_execution.value == "DEMO_EXECUTION"
    assert ExecutionMode.real_execution.value == "REAL_EXECUTION"


# --------------------------------------------------------------------------
# P0-8 — no demo credentials in the shipped frontend
# --------------------------------------------------------------------------


def _web_src():
    from pathlib import Path

    return Path(__file__).resolve().parents[3] / "apps" / "web" / "src"


def test_no_demo_credentials_in_frontend_source():
    """The production bundle is built from src/. It must contain no demo credentials."""
    offenders = []
    for path in list(_web_src().rglob("*.tsx")) + list(_web_src().rglob("*.ts")):
        text = path.read_text(encoding="utf-8")
        if "demo@growthos.ai" in text or "demo1234" in text:
            offenders.append(str(path))
    assert not offenders, f"demo credentials must not appear in frontend source: {offenders}"


def test_login_fields_start_empty():
    login = _web_src() / "app" / "(auth)" / "login" / "page.tsx"
    source = login.read_text(encoding="utf-8")
    assert 'useState("")' in source
    assert 'useState("demo' not in source, "login fields must not be prefilled"


def test_demo_login_helper_is_gated_to_development():
    login = _web_src() / "app" / "(auth)" / "login" / "page.tsx"
    source = login.read_text(encoding="utf-8")
    assert 'NEXT_PUBLIC_ENVIRONMENT === "development"' in source
    assert "NEXT_PUBLIC_DEMO_EMAIL" in source
    assert "NEXT_PUBLIC_DEMO_PASSWORD" in source


def test_env_example_ships_demo_credentials_empty():
    from pathlib import Path

    example = (Path(__file__).resolve().parents[3] / ".env.example").read_text(encoding="utf-8")
    for line in example.splitlines():
        stripped = line.split("#")[0].strip()
        if stripped.startswith(("NEXT_PUBLIC_DEMO_EMAIL", "NEXT_PUBLIC_DEMO_PASSWORD")):
            assert stripped.endswith("="), f"{stripped!r} must ship empty"


# --------------------------------------------------------------------------
# P0-4 — mock AI must be impossible in production
# --------------------------------------------------------------------------


def test_production_rejects_mock_ai_provider():
    from app.core.startup_checks import ConfigurationError, validate_configuration

    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(_settings(ai_provider="mock"))
    assert "AI_PROVIDER" in str(exc.value)


def test_production_rejects_provider_without_api_key():
    from app.core.startup_checks import ConfigurationError, validate_configuration

    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(_settings(ai_provider="openai", openai_api_key=""))
    assert "OPENAI_API_KEY" in str(exc.value)


def test_unknown_ai_provider_raises_instead_of_falling_back(monkeypatch):
    """An unrecognized provider must never silently become the mock provider."""
    from app.ai.providers import factory

    monkeypatch.setattr(factory, "get_settings", lambda: _settings(environment="development", ai_provider="gpt-9000"))
    with pytest.raises(factory.AIProviderConfigurationError) as exc:
        factory.get_ai_provider()
    assert "gpt-9000" in str(exc.value)


def test_mock_provider_blocked_in_production(monkeypatch):
    from app.ai.providers import factory

    monkeypatch.setattr(factory, "get_settings", lambda: _settings(ai_provider="mock"))
    with pytest.raises(factory.AIProviderConfigurationError):
        factory.get_ai_provider()


def test_mock_provider_allowed_in_development(monkeypatch):
    from app.ai.providers import factory
    from app.ai.providers.mock import MockAIProvider

    monkeypatch.setattr(factory, "get_settings", lambda: _settings(environment="development", ai_provider="mock"))
    # The factory returns the provider wrapped in the usage meter (P1-8).
    assert isinstance(factory.get_ai_provider().inner, MockAIProvider)


def test_openai_provider_without_key_is_configuration_error(monkeypatch):
    from app.ai.providers import factory

    monkeypatch.setattr(
        factory,
        "get_settings",
        lambda: _settings(environment="development", ai_provider="openai", openai_api_key=""),
    )
    with pytest.raises(factory.AIProviderConfigurationError) as exc:
        factory.get_ai_provider()
    assert "OPENAI_API_KEY" in str(exc.value)
