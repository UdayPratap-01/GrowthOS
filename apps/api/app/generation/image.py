from __future__ import annotations

from app.core.config import get_settings
from app.generation.base import GenerationResult, ImageGenerationProvider
from app.generation.media_utils import make_demo_png, parse_aspect_ratio
from app.generation.openai_image import OpenAIImageProvider


class UnconfiguredImageProvider(ImageGenerationProvider):
    name = "none"

    def configured(self) -> bool:
        return False

    async def generate_image(
        self, *, prompt: str, width: int = 1024, height: int = 1024, meta: dict | None = None
    ) -> GenerationResult:
        return GenerationResult(
            success=False,
            status="not_configured",
            provider=self.name,
            message="IMAGE GENERATION NOT CONFIGURED",
            error="IMAGE GENERATION NOT CONFIGURED",
            error_code="NOT_CONFIGURED",
            retryable=False,
        )

    async def generate_variations(self, *, prompt: str, count: int = 3, meta: dict | None = None) -> GenerationResult:
        return await self.generate_image(prompt=prompt, meta=meta)

    async def get_status(self, job_id: str) -> GenerationResult:
        return await self.generate_image(prompt="")


class DemoImageProvider(ImageGenerationProvider):
    """DEMO only — produces a real PNG file labeled DEMO. Never used in LIVE mode."""

    name = "demo"

    def configured(self) -> bool:
        return bool(get_settings().demo_mode)

    async def generate_image(
        self, *, prompt: str, width: int = 1024, height: int = 1024, meta: dict | None = None
    ) -> GenerationResult:
        if not get_settings().demo_mode:
            return GenerationResult(
                success=False,
                status="not_configured",
                provider=self.name,
                message="DEMO image provider blocked in LIVE mode",
                error="DEMO_BLOCKED_IN_LIVE",
                error_code="DEMO_BLOCKED_IN_LIVE",
            )
        aspect = (meta or {}).get("aspect_ratio") or "1:1"
        w, h = parse_aspect_ratio(str(aspect), default=(min(width, 512), min(height, 512)))
        w, h = min(w, 512), min(h, 512)
        png = make_demo_png(w, h, label="DEMO")
        return GenerationResult(
            success=True,
            status="completed",
            provider=self.name,
            message="DEMO — real PNG generated and labeled DEMO (not a live provider)",
            media_bytes=png,
            mime_type="image/png",
            width=w,
            height=h,
            assets=[{"type": "image", "note": "DEMO", "prompt": prompt}],
            demo=True,
        )

    async def generate_variations(self, *, prompt: str, count: int = 3, meta: dict | None = None) -> GenerationResult:
        return await self.generate_image(prompt=prompt, meta=meta)

    async def get_status(self, job_id: str) -> GenerationResult:
        return GenerationResult(
            success=True, status="completed", provider=self.name, message="DEMO", demo=True, external_id=job_id
        )


def get_image_provider() -> ImageGenerationProvider:
    settings = get_settings()
    provider = (settings.image_provider or "none").lower().strip()
    if provider in {"", "none", "off"}:
        return UnconfiguredImageProvider()
    if provider == "demo":
        if settings.demo_mode:
            return DemoImageProvider()
        return UnconfiguredImageProvider()
    if provider in {"openai", "dall-e", "dalle"}:
        return OpenAIImageProvider()
    return UnconfiguredImageProvider()
