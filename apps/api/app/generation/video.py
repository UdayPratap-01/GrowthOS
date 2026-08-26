from __future__ import annotations

from app.core.config import get_settings
from app.generation.base import GenerationResult, VideoGenerationProvider
from app.generation.replicate_video import ReplicateVideoProvider


class UnconfiguredVideoProvider(VideoGenerationProvider):
    name = "none"

    def configured(self) -> bool:
        return False

    async def generate_video(
        self,
        *,
        prompt: str,
        duration_seconds: int = 10,
        aspect_ratio: str = "9:16",
        meta: dict | None = None,
    ) -> GenerationResult:
        return GenerationResult(
            success=False,
            status="not_configured",
            provider=self.name,
            message="VIDEO GENERATION NOT CONFIGURED",
            error="VIDEO GENERATION NOT CONFIGURED",
            error_code="NOT_CONFIGURED",
            retryable=False,
        )

    async def generate_variation(self, *, prompt: str, meta: dict | None = None) -> GenerationResult:
        return await self.generate_video(prompt=prompt, meta=meta)

    async def get_status(self, provider_job_id: str) -> GenerationResult:
        return await self.generate_video(prompt="")

    async def get_result(self, provider_job_id: str) -> GenerationResult:
        return await self.generate_video(prompt="")


class DemoVideoProvider(VideoGenerationProvider):
    """
    DEMO only. Does NOT fabricate an MP4.
    Returns an honest failed/not-available for playable video — demo mode may only
    store storyboard metadata unless a real short demo fixture exists.
    We intentionally do not claim COMPLETED video without valid video bytes.
    """

    name = "demo"

    def configured(self) -> bool:
        return bool(get_settings().demo_mode)

    async def generate_video(
        self,
        *,
        prompt: str,
        duration_seconds: int = 10,
        aspect_ratio: str = "9:16",
        meta: dict | None = None,
    ) -> GenerationResult:
        if not get_settings().demo_mode:
            return GenerationResult(
                success=False,
                status="not_configured",
                provider=self.name,
                message="DEMO video provider blocked in LIVE mode",
                error="DEMO_BLOCKED_IN_LIVE",
                error_code="DEMO_BLOCKED_IN_LIVE",
            )
        # Honest: no fake MP4. Storyboard only — status failed for "media file" pipeline
        # with clear DEMO message so callers do not mark COMPLETED without a file.
        return GenerationResult(
            success=False,
            status="failed",
            provider=self.name,
            message="DEMO — no playable video file generated. Configure VIDEO_PROVIDER=replicate with VIDEO_API_KEY.",
            error="DEMO_VIDEO_FILE_NOT_GENERATED",
            error_code="DEMO_VIDEO_FILE_NOT_GENERATED",
            retryable=False,
            demo=True,
            assets=[{"type": "storyboard", "prompt": prompt, "note": "DEMO", "duration_seconds": duration_seconds}],
        )

    async def generate_variation(self, *, prompt: str, meta: dict | None = None) -> GenerationResult:
        return await self.generate_video(prompt=prompt, meta=meta)

    async def get_status(self, provider_job_id: str) -> GenerationResult:
        return await self.generate_video(prompt="")

    async def get_result(self, provider_job_id: str) -> GenerationResult:
        return await self.generate_video(prompt="")


def get_video_provider() -> VideoGenerationProvider:
    settings = get_settings()
    provider = (settings.video_provider or "none").lower().strip()
    if provider in {"", "none", "off"}:
        return UnconfiguredVideoProvider()
    if provider == "demo":
        if settings.demo_mode:
            return DemoVideoProvider()
        return UnconfiguredVideoProvider()
    if provider == "replicate":
        return ReplicateVideoProvider()
    return UnconfiguredVideoProvider()
