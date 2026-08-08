from __future__ import annotations

from app.core.config import get_settings
from app.generation.base import GenerationResult, ImageGenerationProvider


class UnconfiguredImageProvider(ImageGenerationProvider):
    name = "none"

    def configured(self) -> bool:
        return False

    async def generate_image(self, *, prompt: str, meta: dict | None = None) -> GenerationResult:
        return GenerationResult(
            success=False,
            status="not_configured",
            provider=self.name,
            message="IMAGE GENERATION NOT CONFIGURED",
            error="IMAGE GENERATION NOT CONFIGURED",
        )

    async def generate_variations(self, *, prompt: str, count: int = 3, meta: dict | None = None) -> GenerationResult:
        return await self.generate_image(prompt=prompt, meta=meta)

    async def get_status(self, job_id: str) -> GenerationResult:
        return await self.generate_image(prompt="")


class DemoImageProvider(ImageGenerationProvider):
    """Explicit demo-only provider — never claims a real image API success."""

    name = "demo"

    def configured(self) -> bool:
        return True

    async def generate_image(self, *, prompt: str, meta: dict | None = None) -> GenerationResult:
        return GenerationResult(
            success=True,
            status="completed",
            provider=self.name,
            message="DEMO DATA — concept brief only; no real image bytes generated.",
            assets=[
                {
                    "type": "concept",
                    "prompt": prompt,
                    "note": "DEMO DATA",
                    "storage_key": None,
                }
            ],
            demo=True,
        )

    async def generate_variations(self, *, prompt: str, count: int = 3, meta: dict | None = None) -> GenerationResult:
        base = await self.generate_image(prompt=prompt, meta=meta)
        base.assets = [
            {"type": "concept", "prompt": f"{prompt} — variation {i+1}", "note": "DEMO DATA"}
            for i in range(max(1, min(count, 5)))
        ]
        return base

    async def get_status(self, job_id: str) -> GenerationResult:
        return GenerationResult(
            success=True,
            status="completed",
            provider=self.name,
            message="DEMO DATA",
            demo=True,
            external_id=job_id,
        )


def get_image_provider() -> ImageGenerationProvider:
    settings = get_settings()
    provider = (getattr(settings, "image_provider", None) or "none").lower()
    if provider == "demo" and settings.demo_mode:
        return DemoImageProvider()
    # Future: openai / stability / etc. Never claim success without a real adapter.
    return UnconfiguredImageProvider()
