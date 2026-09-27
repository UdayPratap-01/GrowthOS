"""M9.19 — Meta Page discovery, persistence, and tenant-safe token handling."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from app.integrations.meta_oauth import (
    build_meta_connection_config,
    build_meta_token_payload,
    discover_meta_pages,
)
from app.services.lead_ingest_service import _access_token
from app.models.ai_ops import Integration
from app.security.secrets import get_secret_store


class FakeResp:
    def __init__(self, status_code: int, data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._data = data or {}
        self.content = b"1" if data is not None or text else b""
        self.text = text or json.dumps(self._data)

    def json(self) -> dict:
        return self._data


def test_build_meta_connection_config_single_page():
    cfg = build_meta_connection_config(
        me={"id": "user-1", "name": "User"},
        ad_accounts=[{"id": "act_111", "name": "Ads", "account_id": "111"}],
        pages=[{"id": "page-99", "name": "My Page", "access_token": "secret", "tasks": ["ADVERTISE"]}],
        display_name="Meta",
    )
    assert cfg["page_id"] == "page-99"
    assert cfg["page_ids"] == ["page-99"]
    assert cfg["pages"][0]["id"] == "page-99"
    assert cfg["lead_ads"]["webhook_routing_ready"] is True
    assert cfg["lead_ads"]["page_selection_required"] is False
    dumped = json.dumps(cfg)
    assert "access_token" not in dumped
    assert "secret" not in dumped


def test_build_meta_connection_config_multiple_pages_no_primary():
    cfg = build_meta_connection_config(
        me={"id": "user-1", "name": "User"},
        ad_accounts=[],
        pages=[
            {"id": "page-a", "name": "A", "access_token": "tok-a"},
            {"id": "page-b", "name": "B", "access_token": "tok-b"},
        ],
        display_name="Meta",
    )
    assert cfg["page_id"] is None
    assert set(cfg["page_ids"]) == {"page-a", "page-b"}
    assert cfg["lead_ads"]["page_selection_required"] is True
    assert cfg["lead_ads"]["webhook_routing_ready"] is True


def test_build_meta_connection_config_zero_pages():
    cfg = build_meta_connection_config(
        me={"id": "user-1", "name": "User"},
        ad_accounts=[],
        pages=[],
        display_name="Meta",
    )
    assert cfg["page_id"] is None
    assert cfg["page_ids"] == []
    assert cfg["lead_ads"]["webhook_routing_ready"] is False


def test_build_meta_token_payload_encrypts_page_tokens():
    payload = build_meta_token_payload(
        access_token="user-token",
        token_type="bearer",
        expires_in=3600,
        long_lived=True,
        provider="meta",
        pages=[
            {"id": "page-a", "access_token": "page-token-a"},
            {"id": "page-b", "access_token": "page-token-b"},
        ],
    )
    assert payload["access_token"] == "user-token"
    assert payload["page_access_tokens"] == {"page-a": "page-token-a", "page-b": "page-token-b"}
    assert "page_access_token" not in payload


def test_build_meta_token_payload_single_page_compat():
    payload = build_meta_token_payload(
        access_token="user-token",
        token_type="bearer",
        expires_in=3600,
        long_lived=True,
        provider="meta",
        pages=[{"id": "page-1", "access_token": "page-token-1"}],
    )
    assert payload["page_access_token"] == "page-token-1"


def test_access_token_prefers_page_specific_token():
    row = Integration(provider="meta", status="connected")
    row.secret_ref = get_secret_store().store(
        json.dumps(
            {
                "access_token": "user-token",
                "page_access_tokens": {"page-a": "page-token-a", "page-b": "page-token-b"},
            }
        )
    )
    assert _access_token(row, "page-b") == "page-token-b"
    assert _access_token(row, "page-a") == "page-token-a"
    assert _access_token(row, "unknown") == "user-token"


@pytest.mark.asyncio
async def test_discover_meta_pages_one_page(monkeypatch):
    class Client:
        async def get(self, url, params=None):
            assert "/me/accounts" in url
            return FakeResp(
                200,
                {
                    "data": [
                        {
                            "id": "111",
                            "name": "Test Page",
                            "access_token": "page-tok",
                            "tasks": ["ADVERTISE"],
                            "category": "Business",
                        }
                    ]
                },
            )

        async def aclose(self):
            return None

    pages = await discover_meta_pages("user-token", http_client=Client())
    assert len(pages) == 1
    assert pages[0]["id"] == "111"
    assert pages[0]["access_token"] == "page-tok"


@pytest.mark.asyncio
async def test_discover_meta_pages_pagination():
    calls = {"n": 0}

    class Client:
        async def get(self, url, params=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return FakeResp(
                    200,
                    {
                        "data": [{"id": "1", "name": "P1", "access_token": "t1"}],
                        "paging": {"next": "https://graph.facebook.com/v25.0/me/accounts?after=cursor"},
                    },
                )
            return FakeResp(200, {"data": [{"id": "2", "name": "P2", "access_token": "t2"}]})

        async def aclose(self):
            return None

    pages = await discover_meta_pages("user-token", http_client=Client())
    assert [p["id"] for p in pages] == ["1", "2"]


@pytest.mark.asyncio
async def test_discover_meta_pages_authorization_error():
    class Client:
        async def get(self, url, params=None):
            return FakeResp(403, {"error": {"message": "permission denied"}})

        async def aclose(self):
            return None

    with pytest.raises(RuntimeError, match="Meta pages discovery failed"):
        await discover_meta_pages("bad-token", http_client=Client())
