"""M9.18 — Google Ads API modernization tests."""

from __future__ import annotations

import pathlib

import pytest

from app.core.config import get_settings
from app.integrations.google_ads import GoogleAdsIntegration
from app.integrations.google_ads_api import (
    GOOGLE_ADS_API_BASE,
    GOOGLE_ADS_API_VERSION,
    google_ads_headers,
    google_ads_url,
    google_oauth_configured,
)
from app.publishing.provider_errors import classify_google_ads_error


def test_google_ads_api_version_is_current_supported():
    assert GOOGLE_ADS_API_VERSION == "v25"
    assert GOOGLE_ADS_API_BASE == "https://googleads.googleapis.com/v25"


def test_no_stale_v18_references_in_api_code():
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    stale: list[str] = []
    for path in root.rglob("*.py"):
        if path.name == "google_ads_api.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "googleads.googleapis.com/v18" in text or '"/v18/' in text:
            stale.append(str(path.relative_to(root.parent)))
    assert stale == []


def test_oauth_configured_without_developer_token(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    monkeypatch.setattr(settings, "google_ads_developer_token", "")
    assert google_oauth_configured(settings) is True
    assert GoogleAdsIntegration().credentials_configured() is True


def test_google_ads_headers_optional_developer_token(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_ads_developer_token", "")
    headers = google_ads_headers("access", settings=settings)
    assert headers["Authorization"] == "Bearer access"
    assert "developer-token" not in headers

    monkeypatch.setattr(settings, "google_ads_developer_token", "legacy-token")
    headers_with = google_ads_headers("access", settings=settings)
    assert headers_with["developer-token"] == "legacy-token"


def test_google_ads_url_builder():
    assert google_ads_url("/customers:listAccessibleCustomers").endswith("/v25/customers:listAccessibleCustomers")
    assert google_ads_url("customers/1/googleAds:search").endswith("/v25/customers/1/googleAds:search")


def test_classify_cloud_project_not_approved():
    code, cat = classify_google_ads_error(
        status_code=403,
        body={
            "error": {
                "status": "PERMISSION_DENIED",
                "message": "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION",
            }
        },
    )
    assert code == "AUTHORIZATION_ERROR"
    assert cat == "AUTHORIZATION"
