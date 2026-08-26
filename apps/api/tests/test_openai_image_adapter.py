"""
OpenAI Images adapter — DALL·E vs gpt-image request shape.

These cover the compatibility branch that keeps one endpoint working for both
families: size presets differ, and `response_format` is DALL·E-only. They do
not call a live vendor; httpx is pointed at a MockTransport that records the
outbound JSON and returns a real PNG as b64_json.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.generation import openai_image as openai_image_module
from app.generation.media_utils import is_gpt_image_model, make_demo_png, openai_image_size
from app.generation.openai_image import OpenAIImageProvider


# ---------------------------------------------------------------------------
# is_gpt_image_model
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


# ---------------------------------------------------------------------------
# openai_image_size
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
        ("dall-e-3", "16:9", "1792x1024"),
        ("dall-e-3", "9:16", "1024x1792"),
        ("dall-e-3", "1:1", "1024x1024"),
        ("dall-e-2", "16:9", "1792x1024"),
        ("", "16:9", "1792x1024"),
    ],
)
def test_openai_image_size_by_family(model: str, aspect: str, expected: str) -> None:
    assert openai_image_size(aspect, model) == expected


# ---------------------------------------------------------------------------
# Request payload (MockTransport)
# ---------------------------------------------------------------------------


def _png_b64() -> str:
    return base64.b64encode(make_demo_png(64, 64)).decode()


def _install_capture(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """
    Route OpenAIImageProvider's httpx client through a MockTransport that
    records the JSON body of each POST. Same module-level shim pattern as
    scripts/verify_real_media.py so only the adapter is affected.
    """
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            json={"data": [{"b64_json": _png_b64()}]},
        )

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
async def test_gpt_image_payload_omits_response_format_and_uses_family_size(monkeypatch):
    captured = _install_capture(monkeypatch)
    provider = OpenAIImageProvider(api_key="test-key", model="gpt-image-1")

    result = await provider.generate_image(
        prompt="ceramic cup on a wooden table",
        meta={"aspect_ratio": "16:9"},
    )

    assert result.success is True
    assert len(captured) == 1
    payload = captured[0]
    assert payload["model"] == "gpt-image-1"
    assert payload["size"] == "1536x1024"
    assert "response_format" not in payload


@pytest.mark.asyncio
async def test_dalle_payload_keeps_response_format_and_dalle_size(monkeypatch):
    captured = _install_capture(monkeypatch)
    provider = OpenAIImageProvider(api_key="test-key", model="dall-e-3")

    result = await provider.generate_image(
        prompt="ceramic cup on a wooden table",
        meta={"aspect_ratio": "9:16"},
    )

    assert result.success is True
    assert len(captured) == 1
    payload = captured[0]
    assert payload["model"] == "dall-e-3"
    assert payload["size"] == "1024x1792"
    assert payload["response_format"] == "b64_json"


@pytest.mark.asyncio
async def test_chatgpt_image_latest_omits_response_format(monkeypatch):
    captured = _install_capture(monkeypatch)
    provider = OpenAIImageProvider(api_key="test-key", model="chatgpt-image-latest")

    result = await provider.generate_image(
        prompt="square product shot",
        meta={"aspect_ratio": "1:1"},
    )

    assert result.success is True
    assert captured[0]["size"] == "1024x1024"
    assert "response_format" not in captured[0]
