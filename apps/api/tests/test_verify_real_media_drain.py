"""
Queue-drain budgets for `scripts/verify_real_media.py`.

The media-chain drain used to stop after 60 worker cycles. With scheduled polls
advanced to "now", that window is only ~30s — shorter than a real async video
generation — so VENDOR runs declared incomplete while Replicate was still
processing. These tests lock the time-budget behaviour without live vendors.
"""

from __future__ import annotations

import importlib.util
import inspect
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.core.config import get_settings

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_real_media.py"


def _load_verify_script():
    import sys

    name = "verify_real_media_script"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def verify():
    return _load_verify_script()


def _busy_worker(monkeypatch, verify, *, calls: dict):
    class FakeWorker:
        def __init__(self, **_kwargs):
            pass

        async def run_once(self) -> int:
            calls["n"] += 1
            return 1

    monkeypatch.setattr(verify, "Worker", FakeWorker)
    monkeypatch.setattr(verify, "advance_scheduled_jobs", AsyncMock(return_value=0))


def _idle_worker(monkeypatch, verify):
    class FakeWorker:
        def __init__(self, **_kwargs):
            pass

        async def run_once(self) -> int:
            return 0

    monkeypatch.setattr(verify, "Worker", FakeWorker)
    monkeypatch.setattr(verify, "advance_scheduled_jobs", AsyncMock(return_value=0))


@pytest.mark.asyncio
async def test_drain_queue_requires_a_bound(verify) -> None:
    with pytest.raises(ValueError, match="max_cycles and/or timeout_seconds"):
        await verify.drain_queue(max_cycles=None, timeout_seconds=None)


@pytest.mark.asyncio
async def test_drain_queue_time_budget_allows_more_than_sixty_cycles(monkeypatch, verify) -> None:
    """A still-processing video must not be cut off solely by the old cycle cap."""
    calls = {"n": 0}
    _busy_worker(monkeypatch, verify, calls=calls)

    started = time.monotonic()
    executed = await verify.drain_queue(max_cycles=None, timeout_seconds=0.25)
    elapsed = time.monotonic() - started

    assert executed == calls["n"]
    assert calls["n"] > 60
    assert elapsed >= 0.2
    assert elapsed < 2.0


@pytest.mark.asyncio
async def test_drain_queue_respects_timeout(monkeypatch, verify) -> None:
    calls = {"n": 0}
    _busy_worker(monkeypatch, verify, calls=calls)

    started = time.monotonic()
    await verify.drain_queue(max_cycles=None, timeout_seconds=0.15)
    elapsed = time.monotonic() - started

    assert elapsed >= 0.12
    assert elapsed < 1.5
    assert calls["n"] > 0


@pytest.mark.asyncio
async def test_drain_queue_idle_exits_before_timeout(monkeypatch, verify) -> None:
    _idle_worker(monkeypatch, verify)

    started = time.monotonic()
    executed = await verify.drain_queue(max_cycles=1_000, timeout_seconds=30.0)
    elapsed = time.monotonic() - started

    assert executed == 0
    # Three idle sleeps of 0.3s, well under the 30s ceiling.
    assert elapsed < 5.0


@pytest.mark.asyncio
async def test_drain_queue_max_cycles_still_bounds_short_paths(monkeypatch, verify) -> None:
    calls = {"n": 0}
    _busy_worker(monkeypatch, verify, calls=calls)

    executed = await verify.drain_queue(max_cycles=5, follow_scheduled=False)
    assert executed == 5
    assert calls["n"] == 5


def test_phase_media_chain_uses_video_job_timeout_budget(verify) -> None:
    source = inspect.getsource(verify.phase_media_chain)
    assert "video_job_timeout_seconds" in source
    assert "max_cycles=None" in source
    assert "timeout_seconds=" in source
    # Production timeout default remains the configured setting (1800s), not a
    # hardcoded LTX duration.
    assert get_settings().video_job_timeout_seconds >= 60
