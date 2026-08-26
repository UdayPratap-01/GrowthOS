"""
Replicate video adapter — request shape, status mapping, download, cancel.

These prove what `ReplicateVideoProvider` puts on the wire and how it maps
vendor answers. They do not call a live vendor; httpx is pointed at a
MockTransport that records requests and returns controlled JSON / video bytes.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.generation import replicate_video as replicate_video_module
from app.generation.media_utils import is_valid_video
from app.generation.replicate_video import ReplicateVideoProvider

API_KEY = "r8_test_not_a_real_key"
OWNER_MODEL = "acme/demo-video"
VERSION_HASH = "a" * 64
PREDICTION_ID = "pred-verify-123"
OUTPUT_URL = "https://replicate.delivery/verify/out.mp4"
REDIRECT_URL = "https://replicate.delivery/verify/redirected.mp4"

#: Minimal bytes that pass `is_valid_video` (ISO-BMFF ftyp + payload).
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 512
INVALID_BYTES = b"not-a-video-container"


class RecordingVendor:
    """Configurable stand-in for api.replicate.com + delivery hosts."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.submit_status = 201
        self.submit_body: dict[str, Any] = {
            "id": PREDICTION_ID,
            "status": "starting",
        }
        self.poll_status = 200
        self.poll_body: dict[str, Any] = {
            "id": PREDICTION_ID,
            "status": "processing",
        }
        self.cancel_status = 200
        self.cancel_body: dict[str, Any] = {"id": PREDICTION_ID, "status": "canceled"}
        self.download_status = 200
        self.download_body: bytes = FAKE_MP4
        self.redirect_once = False
        self.raise_on_request: Exception | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.raise_on_request is not None:
            raise self.raise_on_request
        self.calls.append(request)
        url = str(request.url)
        method = request.method.upper()

        if method == "POST" and url.endswith("/cancel"):
            return httpx.Response(self.cancel_status, json=self.cancel_body)
        if method == "POST" and "api.replicate.com" in url:
            return httpx.Response(self.submit_status, json=self.submit_body)
        if method == "GET" and f"/predictions/{PREDICTION_ID}" in url:
            return httpx.Response(self.poll_status, json=self.poll_body)
        if method == "GET" and url == OUTPUT_URL and self.redirect_once:
            self.redirect_once = False
            return httpx.Response(302, headers={"Location": REDIRECT_URL})
        if method == "GET" and url in {OUTPUT_URL, REDIRECT_URL}:
            return httpx.Response(self.download_status, content=self.download_body)
        return httpx.Response(404, json={"detail": "unexpected url in stand-in"})

    def to_replicate(self) -> list[httpx.Request]:
        return [c for c in self.calls if "api.replicate.com" in str(c.url)]

    def posts(self) -> list[httpx.Request]:
        return [c for c in self.to_replicate() if c.method.upper() == "POST"]

    def gets(self) -> list[httpx.Request]:
        return [c for c in self.to_replicate() if c.method.upper() == "GET"]


def _install(monkeypatch: pytest.MonkeyPatch, vendor: RecordingVendor) -> RecordingVendor:
    transport = httpx.MockTransport(vendor.handler)

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    class Shim:
        AsyncClient = Client
        TimeoutException = httpx.TimeoutException
        HTTPError = httpx.HTTPError

    monkeypatch.setattr(replicate_video_module, "httpx", Shim)
    return vendor


def _provider(*, model: str = OWNER_MODEL) -> ReplicateVideoProvider:
    return ReplicateVideoProvider(api_key=API_KEY, model=model)


def _auth(request: httpx.Request) -> tuple[str, str]:
    raw = request.headers.get("Authorization", "")
    parts = raw.split(" ", 1)
    if len(parts) != 2:
        return "", ""
    return parts[0], parts[1]


def _json(request: httpx.Request) -> dict:
    return json.loads(request.content.decode())


# ---------------------------------------------------------------------------
# configured()
# ---------------------------------------------------------------------------


def test_configured_requires_key_and_model() -> None:
    # Whitespace-only overrides settings: bare "" is falsy and falls through to env.
    assert ReplicateVideoProvider(api_key=" ", model=OWNER_MODEL).configured() is False
    assert ReplicateVideoProvider(api_key=API_KEY, model=" ").configured() is False
    assert ReplicateVideoProvider(api_key=API_KEY, model=OWNER_MODEL).configured() is True


# ---------------------------------------------------------------------------
# Model routing + submission shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_name_model_uses_models_predictions_endpoint(monkeypatch):
    vendor = _install(monkeypatch, RecordingVendor())
    result = await _provider(model="acme/demo-video").generate_video(
        prompt="storm damage walkthrough",
        duration_seconds=8,
        aspect_ratio="9:16",
    )

    assert result.success is True
    assert result.status == "processing"
    assert result.external_id == PREDICTION_ID
    assert result.media_bytes is None

    posts = vendor.posts()
    assert len(posts) == 1
    assert str(posts[0].url) == "https://api.replicate.com/v1/models/acme/demo-video/predictions"
    assert posts[0].method.upper() == "POST"

    scheme, token = _auth(posts[0])
    assert scheme == "Token"
    assert token == API_KEY
    assert posts[0].headers.get("Content-Type", "").startswith("application/json")
    assert posts[0].headers.get("Prefer") == "wait=0"

    body = _json(posts[0])
    assert "version" not in body
    assert body["input"]["prompt"] == "storm damage walkthrough"
    assert body["input"]["aspect_ratio"] == "9:16"
    assert body["input"]["duration"] == 8


@pytest.mark.asyncio
async def test_version_hash_model_uses_predictions_endpoint(monkeypatch):
    vendor = _install(monkeypatch, RecordingVendor())
    result = await _provider(model=VERSION_HASH).generate_video(prompt="hash route")

    assert result.external_id == PREDICTION_ID
    posts = vendor.posts()
    assert len(posts) == 1
    assert str(posts[0].url) == "https://api.replicate.com/v1/predictions"
    body = _json(posts[0])
    assert body["version"] == VERSION_HASH
    assert body["input"]["prompt"] == "hash route"


@pytest.mark.asyncio
async def test_owner_name_with_version_colon_uses_predictions_endpoint(monkeypatch):
    vendor = _install(monkeypatch, RecordingVendor())
    model = f"acme/demo-video:{VERSION_HASH}"
    await _provider(model=model).generate_video(prompt="colon route")
    posts = vendor.posts()
    assert str(posts[0].url) == "https://api.replicate.com/v1/predictions"
    assert _json(posts[0])["version"] == model


@pytest.mark.asyncio
async def test_provider_input_meta_is_merged_into_input(monkeypatch):
    vendor = _install(monkeypatch, RecordingVendor())
    await _provider().generate_video(
        prompt="base",
        meta={"provider_input": {"fps": 24, "prompt": "override-not-expected-unless-merged"}},
    )
    body = _json(vendor.posts()[0])
    # Meta is spread after the defaults, so provider_input may override prompt.
    assert body["input"]["fps"] == 24
    assert body["input"]["prompt"] == "override-not-expected-unless-merged"


# ---------------------------------------------------------------------------
# LTX model aspect_ratio capabilities (vendor schema: 16:9 | 9:16 only)
# ---------------------------------------------------------------------------

LTX_MODEL = "lightricks/ltx-2.5-fast"


@pytest.mark.parametrize(
    "model,expected",
    [
        ("lightricks/ltx-2.5-fast", True),
        ("lightricks/ltx-2", True),
        ("Lightricks/LTX-2.5-Fast", True),
        ("lightricks/ltx-2.5-fast:deadbeef", True),
        ("acme/demo-video", False),
        (VERSION_HASH, False),
        ("runwayml/gen-4.5", False),
        ("", False),
    ],
)
def test_is_ltx_video_model(model: str, expected: bool) -> None:
    from app.generation.media_utils import is_ltx_video_model

    assert is_ltx_video_model(model) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("aspect", ["16:9", "9:16"])
async def test_ltx_accepts_supported_aspect_ratios(monkeypatch, aspect: str):
    vendor = _install(monkeypatch, RecordingVendor())
    result = await _provider(model=LTX_MODEL).generate_video(
        prompt="ltx ok",
        duration_seconds=10,
        aspect_ratio=aspect,
    )
    assert result.success is True
    assert result.error_code is None
    assert len(vendor.posts()) == 1
    assert _json(vendor.posts()[0])["input"]["aspect_ratio"] == aspect
    assert _json(vendor.posts()[0])["input"]["duration"] == 10
    assert _json(vendor.posts()[0])["input"]["prompt"] == "ltx ok"


@pytest.mark.asyncio
async def test_ltx_rejects_1_1_before_http(monkeypatch):
    vendor = _install(monkeypatch, RecordingVendor())
    result = await _provider(model=LTX_MODEL).generate_video(
        prompt="ltx square",
        duration_seconds=10,
        aspect_ratio="1:1",
    )
    assert result.success is False
    assert result.error_code == "UNSUPPORTED_ASPECT_RATIO"
    assert result.retryable is False
    assert result.external_id is None
    assert "1:1" in (result.error or "")
    assert vendor.posts() == []
    assert vendor.calls == []


@pytest.mark.asyncio
async def test_ltx_rejects_provider_input_override_1_1_before_http(monkeypatch):
    vendor = _install(monkeypatch, RecordingVendor())
    result = await _provider(model=LTX_MODEL).generate_video(
        prompt="ltx override",
        aspect_ratio="9:16",
        meta={"provider_input": {"aspect_ratio": "1:1"}},
    )
    assert result.success is False
    assert result.error_code == "UNSUPPORTED_ASPECT_RATIO"
    assert result.retryable is False
    assert vendor.calls == []


@pytest.mark.asyncio
async def test_non_ltx_model_still_allows_1_1(monkeypatch):
    vendor = _install(monkeypatch, RecordingVendor())
    result = await _provider(model="acme/demo-video").generate_video(
        prompt="square ok elsewhere",
        aspect_ratio="1:1",
    )
    assert result.success is True
    assert len(vendor.posts()) == 1
    assert _json(vendor.posts()[0])["input"]["aspect_ratio"] == "1:1"


@pytest.mark.asyncio
async def test_version_hash_model_not_restricted_to_ltx_ratios(monkeypatch):
    """Opaque version hashes cannot be classified as LTX — do not invent a gate."""
    vendor = _install(monkeypatch, RecordingVendor())
    result = await _provider(model=VERSION_HASH).generate_video(
        prompt="hash square",
        aspect_ratio="1:1",
    )
    assert result.success is True
    assert len(vendor.posts()) == 1


# ---------------------------------------------------------------------------
# Submission error mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_http_429_is_rate_limit(monkeypatch):
    vendor = RecordingVendor()
    vendor.submit_status = 429
    vendor.submit_body = {"detail": "slow down"}
    _install(monkeypatch, vendor)

    result = await _provider().generate_video(prompt="x")
    assert result.success is False
    assert result.error_code == "RATE_LIMIT"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_submit_http_4xx_is_not_retryable(monkeypatch):
    vendor = RecordingVendor()
    vendor.submit_status = 422
    vendor.submit_body = {"detail": "bad input"}
    _install(monkeypatch, vendor)

    result = await _provider().generate_video(prompt="x")
    assert result.success is False
    assert result.error_code == "HTTP_422"
    assert result.retryable is False


@pytest.mark.asyncio
async def test_submit_http_5xx_is_retryable(monkeypatch):
    vendor = RecordingVendor()
    vendor.submit_status = 503
    vendor.submit_body = {"detail": "unavailable"}
    _install(monkeypatch, vendor)

    result = await _provider().generate_video(prompt="x")
    assert result.success is False
    assert result.error_code == "HTTP_503"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_submit_network_error_is_mapped(monkeypatch):
    vendor = RecordingVendor()
    vendor.raise_on_request = httpx.ConnectError("connection refused")
    _install(monkeypatch, vendor)

    result = await _provider().generate_video(prompt="x")
    assert result.success is False
    assert result.error_code == "NETWORK_ERROR"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_submit_without_job_id_fails(monkeypatch):
    vendor = RecordingVendor()
    vendor.submit_body = {"status": "starting"}
    _install(monkeypatch, vendor)

    result = await _provider().generate_video(prompt="x")
    assert result.success is False
    assert result.error_code == "NO_JOB_ID"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_not_configured_refuses_without_http(monkeypatch):
    vendor = _install(monkeypatch, RecordingVendor())
    # Whitespace-only keys override env without falling back to a real credential.
    result = await ReplicateVideoProvider(api_key=" ", model=" ").generate_video(prompt="x")
    assert result.success is False
    assert result.error_code == "NOT_CONFIGURED"
    assert vendor.calls == []


# ---------------------------------------------------------------------------
# Status polling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["starting", "processing"])
async def test_get_status_processing_states(monkeypatch, status: str):
    vendor = RecordingVendor()
    vendor.poll_body = {"id": PREDICTION_ID, "status": status}
    _install(monkeypatch, vendor)

    result = await _provider().get_status(PREDICTION_ID)
    assert result.success is True
    assert result.status == "processing"
    assert result.external_id == PREDICTION_ID
    assert result.media_bytes is None
    assert len(vendor.gets()) == 1
    assert str(vendor.gets()[0].url) == f"https://api.replicate.com/v1/predictions/{PREDICTION_ID}"
    scheme, token = _auth(vendor.gets()[0])
    assert scheme == "Token" and token == API_KEY


@pytest.mark.asyncio
async def test_get_status_succeeded_downloads_string_output(monkeypatch):
    vendor = RecordingVendor()
    vendor.poll_body = {
        "id": PREDICTION_ID,
        "status": "succeeded",
        "output": OUTPUT_URL,
    }
    _install(monkeypatch, vendor)

    result = await _provider().get_status(PREDICTION_ID)
    assert result.success is True
    assert result.status == "completed"
    assert result.media_bytes == FAKE_MP4
    assert is_valid_video(result.media_bytes)
    assert result.mime_type == "video/mp4"
    assert result.download_url == OUTPUT_URL
    assert any(str(c.url) == OUTPUT_URL for c in vendor.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [
        [OUTPUT_URL],
        {"url": OUTPUT_URL},
        {"video": OUTPUT_URL},
    ],
)
async def test_get_status_succeeded_supports_output_shapes(monkeypatch, output):
    vendor = RecordingVendor()
    vendor.poll_body = {"id": PREDICTION_ID, "status": "succeeded", "output": output}
    _install(monkeypatch, vendor)

    result = await _provider().get_status(PREDICTION_ID)
    assert result.success is True
    assert result.status == "completed"
    assert result.media_bytes == FAKE_MP4


@pytest.mark.asyncio
async def test_get_status_failed(monkeypatch):
    vendor = RecordingVendor()
    vendor.poll_body = {"id": PREDICTION_ID, "status": "failed", "error": "nsfw"}
    _install(monkeypatch, vendor)

    result = await _provider().get_status(PREDICTION_ID)
    assert result.success is False
    assert result.status == "failed"
    assert result.error_code == "PROVIDER_FAILED"
    assert result.retryable is False
    assert "nsfw" in (result.error or "")


@pytest.mark.asyncio
async def test_get_status_canceled_is_treated_as_failed(monkeypatch):
    """Current adapter semantics: canceled is a terminal non-success poll result."""
    vendor = RecordingVendor()
    vendor.poll_body = {"id": PREDICTION_ID, "status": "canceled"}
    _install(monkeypatch, vendor)

    result = await _provider().get_status(PREDICTION_ID)
    assert result.success is False
    assert result.status == "failed"
    assert result.error_code == "PROVIDER_FAILED"


@pytest.mark.asyncio
async def test_get_status_unexpected_status_is_failed(monkeypatch):
    vendor = RecordingVendor()
    vendor.poll_body = {"id": PREDICTION_ID, "status": "wat"}
    _install(monkeypatch, vendor)

    result = await _provider().get_status(PREDICTION_ID)
    assert result.success is False
    assert result.status == "failed"
    assert result.error_code == "PROVIDER_FAILED"


@pytest.mark.asyncio
async def test_get_status_http_error(monkeypatch):
    vendor = RecordingVendor()
    vendor.poll_status = 500
    vendor.poll_body = {"detail": "boom"}
    _install(monkeypatch, vendor)

    result = await _provider().get_status(PREDICTION_ID)
    assert result.success is False
    assert result.error_code == "HTTP_500"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_submit_succeeded_materializes_immediately(monkeypatch):
    vendor = RecordingVendor()
    vendor.submit_body = {
        "id": PREDICTION_ID,
        "status": "succeeded",
        "output": OUTPUT_URL,
    }
    _install(monkeypatch, vendor)

    result = await _provider().generate_video(prompt="already done")
    assert result.success is True
    assert result.status == "completed"
    assert result.media_bytes == FAKE_MP4


@pytest.mark.asyncio
async def test_submit_failed_status_is_failed(monkeypatch):
    vendor = RecordingVendor()
    vendor.submit_body = {"id": PREDICTION_ID, "status": "failed", "error": "bad"}
    _install(monkeypatch, vendor)

    result = await _provider().generate_video(prompt="x")
    assert result.success is False
    assert result.status == "failed"
    assert result.error_code == "PROVIDER_FAILED"
    # Current adapter marks provider failures retryable on submit.
    assert result.retryable is True


# ---------------------------------------------------------------------------
# Download / materialization failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_follows_redirect_to_video_bytes(monkeypatch):
    vendor = RecordingVendor()
    vendor.redirect_once = True
    vendor.poll_body = {"id": PREDICTION_ID, "status": "succeeded", "output": OUTPUT_URL}
    _install(monkeypatch, vendor)

    result = await _provider().get_status(PREDICTION_ID)
    assert result.success is True
    assert result.media_bytes == FAKE_MP4
    urls = [str(c.url) for c in vendor.calls if c.method.upper() == "GET"]
    assert OUTPUT_URL in urls
    assert REDIRECT_URL in urls


@pytest.mark.asyncio
async def test_missing_output_url_fails(monkeypatch):
    vendor = RecordingVendor()
    vendor.poll_body = {"id": PREDICTION_ID, "status": "succeeded", "output": None}
    _install(monkeypatch, vendor)

    result = await _provider().get_status(PREDICTION_ID)
    assert result.success is False
    assert result.error_code == "NO_OUTPUT_URL"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_invalid_downloaded_bytes_fail_validation(monkeypatch):
    vendor = RecordingVendor()
    vendor.download_body = INVALID_BYTES
    vendor.poll_body = {"id": PREDICTION_ID, "status": "succeeded", "output": OUTPUT_URL}
    _install(monkeypatch, vendor)

    result = await _provider().get_status(PREDICTION_ID)
    assert result.success is False
    assert result.error_code == "INVALID_VIDEO"
    assert result.retryable is True
    assert not is_valid_video(INVALID_BYTES)


@pytest.mark.asyncio
async def test_download_http_error_status(monkeypatch):
    vendor = RecordingVendor()
    vendor.download_status = 403
    vendor.poll_body = {"id": PREDICTION_ID, "status": "succeeded", "output": OUTPUT_URL}
    _install(monkeypatch, vendor)

    result = await _provider().get_status(PREDICTION_ID)
    assert result.success is False
    assert result.error_code == "INVALID_VIDEO"


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_success_requires_2xx(monkeypatch):
    vendor = _install(monkeypatch, RecordingVendor())
    result = await _provider().cancel(PREDICTION_ID)

    assert result.success is True
    assert result.status == "cancelled"
    assert result.external_id == PREDICTION_ID

    cancel_calls = [c for c in vendor.calls if str(c.url).endswith("/cancel")]
    assert len(cancel_calls) == 1
    assert cancel_calls[0].method.upper() == "POST"
    assert str(cancel_calls[0].url) == (
        f"https://api.replicate.com/v1/predictions/{PREDICTION_ID}/cancel"
    )
    scheme, token = _auth(cancel_calls[0])
    assert scheme == "Token" and token == API_KEY


@pytest.mark.asyncio
async def test_cancel_refused_on_non_2xx(monkeypatch):
    vendor = RecordingVendor()
    vendor.cancel_status = 409
    vendor.cancel_body = {"detail": "cannot cancel this prediction"}
    _install(monkeypatch, vendor)

    result = await _provider().cancel(PREDICTION_ID)
    assert result.success is False
    assert result.status == "failed"
    assert result.error_code == "HTTP_409"
    assert result.external_id == PREDICTION_ID


@pytest.mark.asyncio
async def test_cancel_network_error(monkeypatch):
    vendor = RecordingVendor()
    vendor.raise_on_request = httpx.ReadTimeout("timeout")
    _install(monkeypatch, vendor)

    result = await _provider().cancel(PREDICTION_ID)
    assert result.success is False
    assert result.error_code == "NETWORK_ERROR"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_get_result_delegates_to_get_status(monkeypatch):
    vendor = RecordingVendor()
    vendor.poll_body = {"id": PREDICTION_ID, "status": "processing"}
    _install(monkeypatch, vendor)

    result = await _provider().get_result(PREDICTION_ID)
    assert result.status == "processing"
    assert len(vendor.gets()) == 1
