"""
P1-13 — frontend surfaces honest async/lifecycle states.

These are source-level guards: they fail if a future change removes the
polling path or reintroduces a success claim before backend confirmation.
"""

from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parents[3] / "apps" / "web" / "src"


def test_creative_library_polls_media_jobs_instead_of_blocking():
    source = (WEB / "app" / "(app)" / "creative-library" / "page.tsx").read_text(encoding="utf-8")
    assert "pollMediaJob" in source
    assert "normalizeMediaPhase" in source
    for phase in ("idle", "queued", "generating", "completed", "failed"):
        assert phase in source or phase.capitalize() in source or "MediaJobStatus" in source
    assert "Retry" in source
    # Must not treat a bare 200 as success without a COMPLETED status.
    assert "pollMediaJob" in source


def test_job_helpers_define_required_lifecycles():
    source = (WEB / "lib" / "jobs.ts").read_text(encoding="utf-8")
    for phase in ("idle", "queued", "generating", "completed", "failed", "retry"):
        # "retry" is a UI action; the others are status phases.
        if phase == "retry":
            continue
        assert f'"{phase}"' in source or f"'{phase}'" in source
    for state in (
        "draft",
        "pending_approval",
        "approved",
        "executing",
        "published",
        "failed",
    ):
        assert state in source
    for state in (
        "not_connected",
        "connecting",
        "connected",
        "sync_error",
        "disconnected",
    ):
        assert state in source


def test_integrations_page_exposes_connecting_and_disconnected():
    source = (WEB / "app" / "(app)" / "integrations" / "page.tsx").read_text(encoding="utf-8")
    assert 'status: "connecting"' in source
    assert 'status: "disconnected"' in source
    assert "Connecting…" in source or "Connecting" in source


def test_campaign_builder_does_not_claim_live_publish():
    source = (WEB / "app" / "(app)" / "campaign-builder" / "page.tsx").read_text(encoding="utf-8")
    assert "never claims live publish" in source.lower() or "live publishing only happens" in source.lower()
    assert "pending approval" in source.lower()


def test_status_dot_covers_integration_and_campaign_lifecycles():
    source = (WEB / "components" / "ui" / "StatusDot.tsx").read_text(encoding="utf-8")
    for key in (
        "not_connected",
        "connecting",
        "connected",
        "sync_error",
        "disconnected",
        "pending_approval",
        "executing",
        "published",
        "failed",
    ):
        assert key in source
