"""
Environment isolation for `scripts/verify_real_media.py`.

Phase 2 (request-shape) used to leave stand-in IMAGE_*/VIDEO_* values in
`os.environ`, so Phase 3 VENDOR mode called `owner/model` with a fake key.
These tests lock the restore contract without contacting any live vendor.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from app.core.config import get_settings

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_real_media.py"

# Synthetic values only — never real credentials.
ORIG_VIDEO_PROVIDER = "replicate"
ORIG_VIDEO_MODEL = "lightricks/ltx-2.5-fast"
ORIG_VIDEO_KEY = "test-video-key-original"
ORIG_IMAGE_PROVIDER = "demo"
ORIG_IMAGE_MODEL = "test-image-model-original"
ORIG_IMAGE_KEY = "test-image-key-original"
ORIG_OPENAI_KEY = "test-openai-key-original"
ORIG_DEMO_MODE = "true"


def _load_verify_script():
    import sys

    name = "verify_real_media_script"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass can resolve cls.__module__.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def verify():
    return _load_verify_script()


@pytest.fixture
def media_env(monkeypatch, verify):
    """Process env as a VENDOR video invocation would supply it."""
    monkeypatch.setenv("DEMO_MODE", ORIG_DEMO_MODE)
    monkeypatch.setenv("VIDEO_PROVIDER", ORIG_VIDEO_PROVIDER)
    monkeypatch.setenv("VIDEO_MODEL", ORIG_VIDEO_MODEL)
    monkeypatch.setenv("VIDEO_API_KEY", ORIG_VIDEO_KEY)
    monkeypatch.setenv("IMAGE_PROVIDER", ORIG_IMAGE_PROVIDER)
    monkeypatch.setenv("IMAGE_MODEL", ORIG_IMAGE_MODEL)
    monkeypatch.setenv("IMAGE_API_KEY", ORIG_IMAGE_KEY)
    monkeypatch.setenv("OPENAI_API_KEY", ORIG_OPENAI_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _assert_original_media_env() -> None:
    assert os.environ.get("VIDEO_PROVIDER") == ORIG_VIDEO_PROVIDER
    assert os.environ.get("VIDEO_MODEL") == ORIG_VIDEO_MODEL
    assert os.environ.get("VIDEO_API_KEY") == ORIG_VIDEO_KEY
    assert os.environ.get("IMAGE_PROVIDER") == ORIG_IMAGE_PROVIDER
    assert os.environ.get("IMAGE_MODEL") == ORIG_IMAGE_MODEL
    assert os.environ.get("IMAGE_API_KEY") == ORIG_IMAGE_KEY
    assert os.environ.get("OPENAI_API_KEY") == ORIG_OPENAI_KEY
    assert os.environ.get("DEMO_MODE") == ORIG_DEMO_MODE


def _assert_settings_match_original() -> None:
    settings = get_settings()
    assert settings.video_provider == ORIG_VIDEO_PROVIDER
    assert settings.video_model == ORIG_VIDEO_MODEL
    assert settings.video_api_key == ORIG_VIDEO_KEY
    assert settings.image_provider == ORIG_IMAGE_PROVIDER
    assert settings.image_model == ORIG_IMAGE_MODEL
    assert settings.image_api_key == ORIG_IMAGE_KEY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_temporary_settings_restores_on_success(media_env, verify) -> None:
    with verify.temporary_settings(
        VIDEO_MODEL="owner/model",
        VIDEO_API_KEY=verify.STANDIN_KEY,
        IMAGE_PROVIDER="openai",
        IMAGE_API_KEY=verify.STANDIN_KEY,
    ):
        assert os.environ["VIDEO_MODEL"] == "owner/model"
        assert os.environ["VIDEO_API_KEY"] == verify.STANDIN_KEY
        assert os.environ["IMAGE_PROVIDER"] == "openai"
    _assert_original_media_env()
    _assert_settings_match_original()


def test_temporary_settings_restores_on_exception(media_env, verify) -> None:
    with pytest.raises(RuntimeError, match="forced failure"):
        with verify.temporary_settings(VIDEO_MODEL="owner/model", VIDEO_API_KEY=verify.STANDIN_KEY):
            assert os.environ["VIDEO_MODEL"] == "owner/model"
            raise RuntimeError("forced failure")
    _assert_original_media_env()
    _assert_settings_match_original()


def test_preserved_environ_restores_cleared_keys(monkeypatch, verify) -> None:
    monkeypatch.setenv("VIDEO_MODEL", ORIG_VIDEO_MODEL)
    get_settings.cache_clear()
    with verify.preserved_environ("VIDEO_MODEL"):
        os.environ.pop("VIDEO_MODEL", None)
        assert "VIDEO_MODEL" not in os.environ
    assert os.environ.get("VIDEO_MODEL") == ORIG_VIDEO_MODEL


# ---------------------------------------------------------------------------
# Phase 1 / Phase 2
# ---------------------------------------------------------------------------


def test_phase_provider_resolution_restores_env(media_env, verify, capsys) -> None:
    report = verify.Report()
    verify.phase_provider_resolution(report)
    _assert_original_media_env()
    _assert_settings_match_original()
    assert not report.failed


@pytest.mark.asyncio
async def test_phase_request_shape_restores_env(media_env, verify, capsys) -> None:
    report = verify.Report()
    await verify.phase_request_shape(report)
    _assert_original_media_env()
    _assert_settings_match_original()
    assert not report.failed
    # Stand-in model must not remain after the phase.
    assert os.environ.get("VIDEO_MODEL") != "owner/model"
    assert os.environ.get("VIDEO_API_KEY") != verify.STANDIN_KEY
    assert os.environ.get("IMAGE_API_KEY") != verify.STANDIN_KEY


@pytest.mark.asyncio
async def test_phase_request_shape_restores_env_on_exception(media_env, verify, monkeypatch) -> None:
    async def boom(*_args, **_kwargs):
        raise RuntimeError("forced adapter failure")

    monkeypatch.setattr(
        verify.openai_image.OpenAIImageProvider,
        "generate_image",
        boom,
    )
    report = verify.Report()
    with pytest.raises(RuntimeError, match="forced adapter failure"):
        await verify.phase_request_shape(report)
    _assert_original_media_env()
    _assert_settings_match_original()


# ---------------------------------------------------------------------------
# Phase 3 VENDOR path must read restored process env, not stand-in values
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase3_vendor_env_uses_restored_process_values(media_env, verify, capsys) -> None:
    """
    Mirrors main(): Phase 2 then temporary_settings(DEMO_MODE=false) for VENDOR.

    After Phase 2, VIDEO_* / IMAGE_* must still be the invocation values so
    get_settings() (what Phase 3 adapters read) is not stand-in.
    """
    report = verify.Report()
    await verify.phase_request_shape(report)
    assert not report.failed

    with verify.temporary_settings(DEMO_MODE="false"):
        settings = get_settings()
        assert settings.demo_mode is False
        assert settings.video_provider == ORIG_VIDEO_PROVIDER
        assert settings.video_model == ORIG_VIDEO_MODEL
        assert settings.video_api_key == ORIG_VIDEO_KEY
        assert settings.image_provider == ORIG_IMAGE_PROVIDER
        assert settings.image_model == ORIG_IMAGE_MODEL
        assert settings.image_api_key == ORIG_IMAGE_KEY
        # Explicitly not the Phase 2 stand-in.
        assert settings.video_model != "owner/model"
        assert settings.video_api_key != verify.STANDIN_KEY
        assert settings.image_api_key != verify.STANDIN_KEY

    assert os.environ.get("DEMO_MODE") == ORIG_DEMO_MODE
    _assert_original_media_env()
