"""Replicate video generation adapter — async job + real MP4 download."""

from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.generation.base import GenerationResult, VideoGenerationProvider
from app.generation.media_utils import is_valid_video, replicate_video_aspect_ratio_error


class ReplicateVideoProvider(VideoGenerationProvider):
    name = "replicate"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.api_key = (api_key or settings.video_api_key or "").strip()
        # Prefer version hash or owner/name:version — user-configured
        self.model = (model or settings.video_model or "").strip()

    def configured(self) -> bool:
        return bool(self.api_key and self.model)

    async def generate_video(
        self,
        *,
        prompt: str,
        duration_seconds: int = 10,
        aspect_ratio: str = "9:16",
        meta: dict | None = None,
    ) -> GenerationResult:
        if not self.configured():
            return GenerationResult(
                success=False,
                status="not_configured",
                provider=self.name,
                message="VIDEO GENERATION NOT CONFIGURED",
                error="VIDEO GENERATION NOT CONFIGURED",
                error_code="NOT_CONFIGURED",
                retryable=False,
            )

        # Model-specific gate before any vendor HTTP call. provider_input may
        # override aspect_ratio on the wire, so validate the effective value.
        provider_input = dict((meta or {}).get("provider_input") or {})
        wire_aspect = str(provider_input.get("aspect_ratio", aspect_ratio))
        aspect_error = replicate_video_aspect_ratio_error(wire_aspect, self.model)
        if aspect_error:
            return GenerationResult(
                success=False,
                status="failed",
                provider=self.name,
                message="Unsupported aspect ratio for this video model",
                error=aspect_error,
                error_code="UNSUPPORTED_ASPECT_RATIO",
                retryable=False,
            )

        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
            "Prefer": "wait=0",
        }
        # Support either full version id or owner/model
        body: dict
        if self.model.count("/") >= 1 and ":" not in self.model and len(self.model) < 80:
            # owner/name — use models predictions endpoint
            owner, name = self.model.split("/", 1)
            url = f"https://api.replicate.com/v1/models/{owner}/{name}/predictions"
            body = {
                "input": {
                    "prompt": prompt,
                    "aspect_ratio": aspect_ratio,
                    "duration": duration_seconds,
                    **provider_input,
                }
            }
        else:
            url = "https://api.replicate.com/v1/predictions"
            body = {
                "version": self.model,
                "input": {
                    "prompt": prompt,
                    "aspect_ratio": aspect_ratio,
                    "duration": duration_seconds,
                    **provider_input,
                },
            }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, headers=headers, json=body)
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
                message="Rate limited by video provider",
                error=resp.text[:300],
                error_code="RATE_LIMIT",
                retryable=True,
            )
        if resp.status_code >= 400:
            return GenerationResult(
                success=False,
                status="failed",
                provider=self.name,
                message="Provider request failed",
                error=resp.text[:500],
                error_code=f"HTTP_{resp.status_code}",
                retryable=resp.status_code >= 500,
            )

        data = resp.json()
        job_id = data.get("id")
        status = (data.get("status") or "starting").lower()
        if not job_id:
            return GenerationResult(
                success=False,
                status="failed",
                provider=self.name,
                message="No provider job id returned",
                error="NO_JOB_ID",
                error_code="NO_JOB_ID",
                retryable=True,
            )
        mapped = "submitted"
        if status in {"starting", "processing"}:
            mapped = "processing"
        elif status == "succeeded":
            mapped = "completed"
        elif status == "failed" or status == "canceled":
            mapped = "failed"

        result = GenerationResult(
            success=mapped != "failed",
            status=mapped,
            provider=self.name,
            message=f"Video job {mapped}",
            external_id=job_id,
            error=data.get("error"),
            error_code="PROVIDER_FAILED" if mapped == "failed" else None,
            retryable=mapped == "failed",
        )
        if mapped == "completed":
            return await self._materialize(data, result)
        return result

    async def generate_variation(self, *, prompt: str, meta: dict | None = None) -> GenerationResult:
        return await self.generate_video(prompt=prompt, meta=meta)

    async def get_status(self, provider_job_id: str) -> GenerationResult:
        if not self.configured():
            return GenerationResult(
                success=False,
                status="not_configured",
                provider=self.name,
                message="VIDEO GENERATION NOT CONFIGURED",
                error="VIDEO GENERATION NOT CONFIGURED",
                error_code="NOT_CONFIGURED",
            )
        headers = {"Authorization": f"Token {self.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(
                    f"https://api.replicate.com/v1/predictions/{provider_job_id}",
                    headers=headers,
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
                external_id=provider_job_id,
            )
        if resp.status_code >= 400:
            return GenerationResult(
                success=False,
                status="failed",
                provider=self.name,
                message="Failed to poll provider job",
                error=resp.text[:400],
                error_code=f"HTTP_{resp.status_code}",
                retryable=resp.status_code >= 500,
                external_id=provider_job_id,
            )
        data = resp.json()
        status = (data.get("status") or "").lower()
        if status in {"starting", "processing"}:
            return GenerationResult(
                success=True,
                status="processing",
                provider=self.name,
                message="Video still processing",
                external_id=provider_job_id,
            )
        if status == "succeeded":
            base = GenerationResult(
                success=True,
                status="completed",
                provider=self.name,
                message="Provider reports completed",
                external_id=provider_job_id,
            )
            return await self._materialize(data, base)
        return GenerationResult(
            success=False,
            status="failed",
            provider=self.name,
            message="Video generation failed",
            error=str(data.get("error") or status)[:400],
            error_code="PROVIDER_FAILED",
            retryable=False,
            external_id=provider_job_id,
        )

    async def get_result(self, provider_job_id: str) -> GenerationResult:
        return await self.get_status(provider_job_id)

    async def cancel(self, provider_job_id: str) -> GenerationResult:
        """
        Stop a running prediction so it stops accruing cost.

        Only a 2xx from the provider counts as cancelled. Anything else is
        reported as a failed cancellation, because a job we believe is stopped
        but which is still running is the worst of both states.
        """
        if not self.configured():
            return GenerationResult(
                success=False,
                status="not_configured",
                provider=self.name,
                message="VIDEO GENERATION NOT CONFIGURED",
                error="VIDEO GENERATION NOT CONFIGURED",
                error_code="NOT_CONFIGURED",
            )
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"https://api.replicate.com/v1/predictions/{provider_job_id}/cancel",
                    headers={"Authorization": f"Token {self.api_key}"},
                )
        except httpx.HTTPError as exc:
            return GenerationResult(
                success=False,
                status="failed",
                provider=self.name,
                message="Provider network error while cancelling",
                error=str(exc)[:300],
                error_code="NETWORK_ERROR",
                retryable=True,
                external_id=provider_job_id,
            )
        if resp.status_code >= 400:
            return GenerationResult(
                success=False,
                status="failed",
                provider=self.name,
                message="Provider refused the cancellation",
                error=resp.text[:300],
                error_code=f"HTTP_{resp.status_code}",
                external_id=provider_job_id,
            )
        return GenerationResult(
            success=True,
            status="cancelled",
            provider=self.name,
            message="Provider confirmed the generation was cancelled.",
            external_id=provider_job_id,
        )

    async def _materialize(self, data: dict, base: GenerationResult) -> GenerationResult:
        output = data.get("output")
        url = None
        if isinstance(output, str):
            url = output
        elif isinstance(output, list) and output:
            url = output[0] if isinstance(output[0], str) else None
        elif isinstance(output, dict):
            url = output.get("url") or output.get("video")
        if not url:
            base.success = False
            base.status = "failed"
            base.error = "NO_OUTPUT_URL"
            base.error_code = "NO_OUTPUT_URL"
            base.retryable = True
            return base

        try:
            async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
                dl = await client.get(url)
            if dl.status_code != 200 or not is_valid_video(dl.content):
                base.success = False
                base.status = "failed"
                base.error = "INVALID_OR_EMPTY_VIDEO"
                base.error_code = "INVALID_VIDEO"
                base.retryable = True
                return base
            base.media_bytes = dl.content
            base.mime_type = "video/mp4"
            base.download_url = url
            base.assets = [{"type": "video", "source_url": url}]
            base.status = "completed"
            base.success = True
            return base
        except httpx.HTTPError as exc:
            base.success = False
            base.status = "failed"
            base.error = str(exc)[:300]
            base.error_code = "DOWNLOAD_FAILED"
            base.retryable = True
            return base
