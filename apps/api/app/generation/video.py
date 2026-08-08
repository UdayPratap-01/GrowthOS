from __future__ import annotations

from app.core.config import get_settings
from app.generation.base import GenerationResult, VideoGenerationProvider


class UnconfiguredVideoProvider(VideoGenerationProvider):
    name = "none"

    def configured(self) -> bool:
        return False

    async def generate_video(self, *, prompt: str, meta: dict | None = None) -> GenerationResult:
        return GenerationResult(
            success=False,
            status="not_configured",
            provider=self.name,
            message="VIDEO GENERATION NOT CONFIGURED",
            error="VIDEO GENERATION NOT CONFIGURED",
        )

    async def generate_variation(self, *, prompt: str, meta: dict | None = None) -> GenerationResult:
        return await self.generate_video(prompt=prompt, meta=meta)

    async def get_status(self, job_id: str) -> GenerationResult:
        return await self.generate_video(prompt="")

    async def get_result(self, job_id: str) -> GenerationResult:
        return await self.generate_video(prompt="")


class DemoVideoProvider(VideoGenerationProvider):
    name = "demo"

    def configured(self) -> bool:
        return True

    async def generate_video(self, *, prompt: str, meta: dict | None = None) -> GenerationResult:
        return GenerationResult(
            success=True,
            status="completed",
            provider=self.name,
            message="DEMO DATA — storyboard only; no real video file generated.",
            assets=[{"type": "storyboard", "prompt": prompt, "note": "DEMO DATA"}],
            demo=True,
        )

    async def generate_variation(self, *, prompt: str, meta: dict | None = None) -> GenerationResult:
        return await self.generate_video(prompt=prompt, meta=meta)

    async def get_status(self, job_id: str) -> GenerationResult:
        return GenerationResult(
            success=True, status="completed", provider=self.name, message="DEMO DATA", demo=True, external_id=job_id
        )

    async def get_result(self, job_id: str) -> GenerationResult:
        return await self.get_status(job_id)


def get_video_provider() -> VideoGenerationProvider:
    settings = get_settings()
    provider = (getattr(settings, "video_provider", None) or "none").lower()
    if provider == "demo" and settings.demo_mode:
        return DemoVideoProvider()
    return UnconfiguredVideoProvider()
