"""Milestone 8 — production deployment packaging guards (no live ads)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
API_ROOT = Path(__file__).resolve().parents[1]


def test_security_headers_on_health_live():
    from app.main import app

    client = TestClient(app)
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("Referrer-Policy") == "no-referrer"
    assert "default-src 'none'" in (response.headers.get("Content-Security-Policy") or "")
    assert "camera=()" in (response.headers.get("Permissions-Policy") or "")


def test_dockerfile_runs_as_non_root():
    content = (API_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER growthos" in content
    assert "useradd" in content
    cmd_lines = [ln for ln in content.splitlines() if ln.startswith("CMD")]
    assert cmd_lines
    assert not any("app.demo.seed" in ln for ln in cmd_lines)


def test_ci_workflow_exists_and_keeps_live_ads_off():
    workflow = ROOT / ".github" / "workflows" / "ci.yml"
    assert workflow.is_file()
    text = workflow.read_text(encoding="utf-8")
    assert "pytest" in text
    assert "alembic check" in text
    assert "AUTONOMOUS_EXECUTION_ENABLED: \"false\"" in text
    assert "CANARY_ENABLED: \"false\"" in text
    assert "npx tsc --noEmit" in text


def test_prod_compose_defaults_disable_live_execution():
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "AUTONOMOUS_EXECUTION_ENABLED: ${AUTONOMOUS_EXECUTION_ENABLED:-false}" in compose
    assert "CANARY_ENABLED: ${CANARY_ENABLED:-false}" in compose
    assert "DEMO_MODE: \"false\"" in compose
    assert "DB_AUTO_CREATE: \"false\"" in compose


def test_live_execution_defaults_remain_off():
    from app.core.config import Settings

    fields = Settings.model_fields
    assert fields["autonomous_execution_enabled"].default is False
    assert fields["canary_enabled"].default is False
    assert fields["provider_verification_enabled"].default is False
    assert fields["meta_autonomous_enabled"].default is False
    assert fields["google_autonomous_enabled"].default is False
    assert fields["optimization_enabled"].default is False
    assert fields["demo_mode"].default is False


@pytest.mark.parametrize(
    "relative",
    [
        "docs/PRODUCTION_RUNBOOK.md",
        "docs/BACKUP_AND_DR.md",
        "docs/SECRET_ROTATION.md",
        "docs/PRODUCTION_READINESS.md",
        "docs/PRODUCTION_TODO.md",
    ],
)
def test_m8_ops_docs_exist(relative: str):
    path = ROOT / relative
    assert path.is_file(), f"missing {relative}"
    text = path.read_text(encoding="utf-8")
    assert "LIVE" in text.upper() or "OFF" in text or "PENDING" in text
