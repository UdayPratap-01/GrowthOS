"""M9.19 — Meta Graph API modernization tests."""

from __future__ import annotations

import pathlib

from app.integrations.meta_graph_api import (
    META_AUTH_URL,
    META_GRAPH,
    META_GRAPH_API_BASE,
    META_GRAPH_API_VERSION,
    META_TOKEN_URL,
    meta_graph_url,
)


def test_meta_graph_api_version_is_current_supported():
    assert META_GRAPH_API_VERSION == "v25.0"
    assert META_GRAPH_API_BASE == "https://graph.facebook.com/v25.0"
    assert META_AUTH_URL == "https://www.facebook.com/v25.0/dialog/oauth"
    assert META_TOKEN_URL == "https://graph.facebook.com/v25.0/oauth/access_token"
    assert META_GRAPH == META_GRAPH_API_BASE


def test_meta_graph_url_builder():
    assert meta_graph_url("123456789").endswith("/v25.0/123456789")
    assert meta_graph_url("/me/accounts").endswith("/v25.0/me/accounts")


def test_no_stale_graph_version_references_in_api_code():
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    stale: list[str] = []
    for path in root.rglob("*.py"):
        if path.name == "meta_graph_api.py":
            continue
        text = path.read_text(encoding="utf-8")
        for needle in (
            "graph.facebook.com/v19.0",
            "graph.facebook.com/v21.0",
            "facebook.com/v21.0/dialog",
        ):
            if needle in text:
                stale.append(f"{path.relative_to(root.parent)}: {needle}")
    assert stale == []
