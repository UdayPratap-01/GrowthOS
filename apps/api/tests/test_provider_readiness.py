"""Unit tests for operator readiness stage helpers (no network)."""

from app.publishing.readiness import (
    STAGE_CANARY_BLOCKED,
    STAGE_CANARY_READY,
    STAGE_CONNECTED,
    STAGE_NOT_CONFIGURED,
    STAGE_NOT_CONNECTED,
    STAGE_VERIFICATION_FAILED,
    STAGE_VERIFICATION_REQUIRED,
    STAGE_VERIFIED,
    derive_provider_readiness_stage,
    production_readiness_summary,
)


def test_not_configured():
    r = derive_provider_readiness_stage(
        credentials_configured=False,
        integration_connected=False,
    )
    assert r["stage"] == STAGE_NOT_CONFIGURED
    assert r["live_provider_verification"] == "NOT_RUN"


def test_not_connected():
    r = derive_provider_readiness_stage(
        credentials_configured=True,
        integration_connected=False,
    )
    assert r["stage"] == STAGE_NOT_CONNECTED


def test_connected_without_verification():
    r = derive_provider_readiness_stage(
        credentials_configured=True,
        integration_connected=True,
        last_verification=None,
    )
    assert r["stage"] == STAGE_CONNECTED


def test_verified():
    r = derive_provider_readiness_stage(
        credentials_configured=True,
        integration_connected=True,
        last_verification={
            "status": "VERIFIED",
            "checked_at": "2099-01-01T00:00:00+00:00",
            "ran": True,
            "live_provider_verification": "RAN",
            "account": {"id": "act_1", "name": "Test"},
            "canary_resources": {"campaigns": [{"id": "120", "name": "C1"}]},
        },
        max_age_hours=24,
    )
    assert r["stage"] == STAGE_VERIFIED
    assert r["campaigns"][0]["id"] == "120"
    assert r["verification_stale"] is False


def test_verification_failed_exposes_reason():
    r = derive_provider_readiness_stage(
        credentials_configured=True,
        integration_connected=True,
        last_verification={
            "status": "VERIFICATION_FAILED",
            "ran": True,
            "error_category": "authentication",
            "steps": [{"step": "me", "ok": False, "detail": "token expired"}],
        },
    )
    assert r["stage"] == STAGE_VERIFICATION_FAILED
    assert r["failure_reason"] == "authentication"


def test_stale_verified_requires_refresh():
    r = derive_provider_readiness_stage(
        credentials_configured=True,
        integration_connected=True,
        last_verification={
            "status": "VERIFIED",
            "checked_at": "2000-01-01T00:00:00+00:00",
            "ran": True,
        },
        max_age_hours=24,
    )
    assert r["stage"] == STAGE_VERIFICATION_REQUIRED
    assert r["verification_stale"] is True


def test_canary_overlay():
    base = {
        "status": "VERIFIED",
        "checked_at": "2099-01-01T00:00:00+00:00",
        "ran": True,
    }
    ready = derive_provider_readiness_stage(
        credentials_configured=True,
        integration_connected=True,
        last_verification=base,
        canary_provider_ready=True,
        canary_enabled=True,
        kill_switch=False,
    )
    assert ready["stage"] == STAGE_CANARY_READY

    blocked = derive_provider_readiness_stage(
        credentials_configured=True,
        integration_connected=True,
        last_verification=base,
        canary_provider_ready=True,
        canary_enabled=True,
        kill_switch=True,
    )
    assert blocked["stage"] == STAGE_CANARY_BLOCKED


def test_production_readiness_summary_pending():
    s = production_readiness_summary(
        meta_credentials=False,
        google_credentials=False,
        meta_stage=STAGE_NOT_CONFIGURED,
        google_stage=STAGE_NOT_CONFIGURED,
        autonomous_execution_enabled=False,
        canary_enabled=False,
        optimization_enabled=False,
        kill_switch=False,
        demo_mode=True,
    )
    assert s["code_ready"] is True
    assert s["real_provider_verification_pending"] is True
    assert s["production_deployment_pending"] is True
    assert s["banners"]["providers"] == "REAL PROVIDER VERIFICATION PENDING"
    assert s["live_mutations_default_off"] is True
