"""OpenAI Images API adapter — real bytes only; never invents success."""

from __future__ import annotations

import base64

import httpx

from app.core.config import get_settings
from app.generation.base import GenerationResult, ImageGenerationProvider
from app.generation.media_utils import is_valid_image, openai_image_size


class OpenAIImageProvider(ImageGenerationProvider):
    name = "openai"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.api_key = (api_key or settings.image_api_key or settings.openai_api_key or "").strip()
        self.model = (model or settings.image_model or "dall-e-3").strip()

    def configured(self) -> bool:
        return bool(self.api_key)

    async def generate_image(
        self,
        *,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        meta: dict | None = None,
    ) -> GenerationResult:
        if not self.configured():
            return GenerationResult(
                success=False,
                status="not_configured",
                provider=self.name,
                message="IMAGE GENERATION NOT CONFIGURED",
                error="IMAGE GENERATION NOT CONFIGURED",
                error_code="NOT_CONFIGURED",
                retryable=False,
            )

        aspect = (meta or {}).get("aspect_ratio") or "1:1"
        size = openai_image_size(str(aspect))
        # dall-e-3 supports size presets; map width/height if provided via size
        payload = {
            "model": self.model,
            "prompt": prompt[:3900],
            "n": 1,
            "size": size,
            "response_format": "b64_json",
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/images/generations",
                    headers=headers,
                    json=payload,
                )
        except httpx.TimeoutException:
            return GenerationResult(
                success=False,
                status="failed",
                provider=self.name,
                message="Provider request timed out",
                error="TIMEOUT",
                error_code="TIMEOUT",
                retryable=True,
            )
        except httpx.HTTPError as exc:
            return GenerationResult(
                success=False,
                status="failed",
                provider=self.name,
                message="Provider network error",
                error=str(exc)[:300],
                error_code="NETWORK_ERROR",
                retryable=True,
            )

        if resp.status_code == 429:
            return GenerationResult(
                success=False,
                status="failed",
                provider=self.name,
                message="Rate limited by image provider",
                error=resp.text[:300],
                error_code="RATE_LIMIT",
                retryable=True,
            )
        if resp.status_code >= 400:
            retryable = resp.status_code >= 500
            return GenerationResult(
                success=False,
                status="failed",
                provider=self.name,
                message="Provider request failed",
                error=resp.text[:500],
                error_code=f"HTTP_{resp.status_code}",
                retryable=retryable,
            )

        data = resp.json()
        items = data.get("data") or []
        if not items:
            return GenerationResult(
                success=False,
                status="failed",
                provider=self.name,
                message="Empty provider response",
                error="EMPTY_RESPONSE",
                error_code="EMPTY_RESPONSE",
                retryable=True,
            )
        item = items[0]
        b64 = item.get("b64_json")
        remote_url = item.get("url")
        media: bytes | None = None
        if b64:
            try:
                media = base64.b64decode(b64)
            except Exception:
                media = None
        elif remote_url:
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    dl = await client.get(remote_url)
                if dl.status_code == 200:
                    media = dl.content
            except httpx.HTTPError as exc:
                return GenerationResult(
                    success=False,
                    status="failed",
                    provider=self.name,
                    message="Failed to download generated image",
                    error=str(exc)[:300],
                    error_code="DOWNLOAD_FAILED",
                    retryable=True,
                )

        if not media or not is_valid_image(media):
            return GenerationResult(
                success=False,
                status="failed",
                provider=self.name,
                message="Provider returned invalid image bytes",
                error="INVALID_IMAGE",
                error_code="INVALID_IMAGE",
                retryable=True,
            )

        w, h = size.split("x")
        return GenerationResult(
            success=True,
            status="completed",
            provider=self.name,
            message="Image generated via OpenAI Images API",
            media_bytes=media,
            mime_type="image/png" if media.startswith(b"\x89PNG") else "image/jpeg",
            width=int(w),
            height=int(h),
            external_id=item.get("revised_prompt") and None,
            assets=[{"type": "image", "model": self.model, "size": size}],
            demo=False,
        )

    async def generate_variations(
        self, *, prompt: str, count: int = 3, meta: dict | None = None
    ) -> GenerationResult:
        # dall-e-3 does not support n>1; generate sequentially via caller
        return await self.generate_image(prompt=prompt, meta=meta)

    async def get_status(self, job_id: str) -> GenerationResult:
        return GenerationResult(
            success=False,
            status="failed",
            provider=self.name,
            message="OpenAI image generation is synchronous; no remote job status",
            error="NO_ASYNC_JOB",
            error_code="NO_ASYNC_JOB",
        )
