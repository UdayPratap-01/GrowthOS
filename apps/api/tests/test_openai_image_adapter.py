"""
OpenAI Images adapter — gpt-image request shape (no response_format).

DALL·E snapshots are retired; the adapter fail-fasts with MODEL_RETIRED.
These tests do not call a live vendor; httpx uses MockTransport.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.core.config import Settings
from app.generation import openai_image as openai_image_module
from app.generation.media_utils import (
    is_gpt_image_model,
    is_retired_openai_image_model,
    make_demo_png,
    openai_image_size,
)
from app.generation.openai_image import OpenAIImageProvider


# ---------------------------------------------------------------------------
# is_gpt_image_model / is_retired_openai_image_model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    [
        "gpt-image-1",
        "gpt-image-1-mini",
        "gpt-image-1.5",
        "gpt-image-2",
        "chatgpt-image-latest",
        "GPT-IMAGE-1",
        "ChatGPT-Image-Latest",
    ],
)
def test_is_gpt_image_model_true(model: str) -> None:
    assert is_gpt_image_model(model) is True


@pytest.mark.parametrize(
    "model",
    [
        "dall-e-3",
        "dall-e-2",
        "DALL-E-3",
        "",
        "openai",
        "stable-diffusion",
    ],
)
def test_is_gpt_image_model_false(model: str) -> None:
    assert is_gpt_image_model(model) is False


@pytest.mark.parametrize("model", ["dall-e-2", "dall-e-3", "DALL-E-3", "dalle-2", "dalle_3"])
def test_is_retired_openai_image_model_true(model: str) -> None:
    assert is_retired_openai_image_model(model) is True


@pytest.mark.parametrize("model", ["gpt-image-1", "chatgpt-image-latest", "", "openai"])
def test_is_retired_openai_image_model_false(model: str) -> None:
    assert is_retired_openai_image_model(model) is False


# ---------------------------------------------------------------------------
# openai_image_size — gpt-image presets only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "aspect", "expected"),
    [
        ("gpt-image-1", "16:9", "1536x1024"),
        ("gpt-image-1-mini", "16:9", "1536x1024"),
        ("chatgpt-image-latest", "16:9", "1536x1024"),
        ("gpt-image-1", "9:16", "1024x1536"),
        ("gpt-image-1", "1:1", "1024x1024"),
        ("GPT-IMAGE-1", "16:9", "1536x1024"),
        # Retired / empty model args still map to current gpt-image sizes
        # (adapter rejects retired models before HTTP; size helper stays current).
        ("dall-e-3", "16:9", "1536x1024"),
        ("dall-e-3", "9:16", "1024x1536"),
        ("dall-e-3", "1:1", "1024x1024"),
        ("dall-e-2", "16:9", "1536x1024"),
        ("", "16:9", "1536x1024"),
    ],
)
def test_openai_image_size_current_presets(model: str, aspect: str, expected: str) -> None:
    assert openai_image_size(aspect, model) == expected


def test_settings_default_image_model_is_gpt_image_1() -> None:
    assert Settings.model_fields["image_model"].default == "gpt-image-1"


# ---------------------------------------------------------------------------
# Request payload (MockTransport)
# ---------------------------------------------------------------------------


def _png_b64() -> str:
    return base64.b64encode(make_demo_png(64, 64)).decode()


def _install_capture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response_factory=None,
) -> list[dict]:
    """
    Route OpenAIImageProvider's httpx client through a MockTransport that
    records the JSON body of each POST.
    """
    captured: list[dict] = []

    def default_factory(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"b64_json": _png_b64()}]},
        )

    factory = response_factory or default_factory

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.content:
            try:
                captured.append(json.loads(request.content.decode()))
            except Exception:
                pass
        return factory(request)

    transport = httpx.MockTransport(handler)

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    class Shim:
        AsyncClient = Client
        TimeoutException = httpx.TimeoutException
        HTTPError = httpx.HTTPError

    monkeypatch.setattr(openai_image_module, "httpx", Shim)
    return captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "aspect", "expected_size"),
    [
        ("gpt-image-1", "16:9", "1536x1024"),
        ("gpt-image-1-mini", "9:16", "1024x1536"),
        ("chatgpt-image-latest", "1:1", "1024x1024"),
        ("gpt-image-1.5", "16:9", "1536x1024"),
    ],
)
async def test_payload_omits_response_format_for_current_models(
    monkeypatch, model: str, aspect: str, expected_size: str
):
    captured = _install_capture(monkeypatch)
    provider = OpenAIImageProvider(api_key="test-key", model=model)

    result = await provider.generate_image(
        prompt="ceramic cup on a wooden table",
        meta={"aspect_ratio": aspect},
    )

    assert result.success is True
    assert len(captured) == 1
    payload = captured[0]
    assert payload["model"] == model
    assert payload["size"] == expected_size
    assert "response_format" not in payload
    assert set(payload.keys()) >= {"model", "prompt", "n", "size"}


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["dall-e-2", "dall-e-3", "DALL-E-3"])
async def test_retired_models_fail_fast_without_http(monkeypatch, model: str):
    captured = _install_capture(monkeypatch)
    provider = OpenAIImageProvider(api_key="test-key", model=model)

    result = await provider.generate_image(
        prompt="should never reach OpenAI",
        meta={"aspect_ratio": "1:1"},
    )

    assert result.success is False
    assert result.error_code == "MODEL_RETIRED"
    assert result.retryable is False
    assert captured == []


@pytest.mark.asyncio
async def test_decodes_b64_json_into_valid_image(monkeypatch):
    png = make_demo_png(64, 64)
    b64 = base64.b64encode(png).decode()

    def factory(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"b64_json": b64}]})

    _install_capture(monkeypatch, response_factory=factory)
    provider = OpenAIImageProvider(api_key="test-key", model="gpt-image-1")

    result = await provider.generate_image(prompt="decode me", meta={"aspect_ratio": "1:1"})

    assert result.success is True
    assert result.media_bytes == png
    assert result.mime_type == "image/png"


@pytest.mark.asyncio
async def test_url_fallback_downloads_when_b64_missing(monkeypatch):
    png = make_demo_png(32, 32)
    calls: list[str] = []

    def factory(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"data": [{"url": "https://cdn.example/img.png"}]},
            )
        return httpx.Response(200, content=png)

    _install_capture(monkeypatch, response_factory=factory)
    provider = OpenAIImageProvider(api_key="test-key", model="gpt-image-1")

    result = await provider.generate_image(prompt="url path", meta={"aspect_ratio": "1:1"})

    assert result.success is True
    assert result.media_bytes == png
    assert any("images/generations" in u for u in calls)
    assert any("cdn.example/img.png" in u for u in calls)
