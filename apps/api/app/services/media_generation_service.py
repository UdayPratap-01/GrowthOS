"""Real media generation pipeline: provider → bytes → storage → DB. No fake COMPLETED."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.core.errors import ProviderError
from app.core.mode import effective_demo_mode
from app.generation import get_image_provider, get_video_provider
from app.generation.media_utils import detect_image_mime, detect_video_mime, is_valid_image, is_valid_video, parse_aspect_ratio
from app.models.automation import CreativeAsset, ImageJob, VideoJob
from app.models.enums import JobStatus
from app.jobs.registry import IMAGE_GENERATE, VIDEO_GENERATE
from app.models.organization import Organization
from app.observability import events, metrics
from app.services.usage_service import Metric, meter
from app.storage import StorageError, get_object_storage


def _api_status(status: JobStatus | str) -> str:
    value = status.value if isinstance(status, JobStatus) else str(status)
    return value.upper()


class MediaGenerationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._storage = None

    @property
    def storage(self):
        # Resolved lazily: a storage misconfiguration should fail the job with a
        # reason, not blow up every request that happens to build this service.
        if self._storage is None:
            self._storage = get_object_storage()
        return self._storage

    async def _dispatch(
        self,
        organization,
        *,
        job_type: str,
        payload: dict,
        dedupe_key: str,
        run_inline,
    ) -> None:
        """
        Hand the work to a worker, or run it here in development.

        Production always enqueues: a video can take minutes, and holding an HTTP
        request open for that loses the job on any restart and ties up a worker
        slot in the web tier. The row is already persisted as QUEUED, so the
        caller gets a job id it can poll either way.
        """
        if get_settings().should_run_jobs_inline:
            await run_inline()
            return

        from app.jobs.queue import JobQueue

        await JobQueue(self.db).enqueue(
            job_type=job_type,
            payload=payload,
            organization_id=organization.id,
            dedupe_key=dedupe_key,
        )

    async def _persist(self, job, data: bytes, key: str, mime: str) -> bool:
        """
        Upload and confirm. Returns False (with the job marked failed) when the
        bytes are not actually retrievable afterwards, so a job is never marked
        COMPLETED for an asset that does not exist.
        """
        try:
            await self.storage.upload(data, key, mime)
            stored = await self.storage.exists(key)
        except StorageError as exc:
            stored = False
            detail = str(exc)
        else:
            detail = "Upload reported success but the object could not be found afterwards."
        if stored:
            # Successes are counted too, or a failure count has no denominator
            # and "3 storage errors" cannot be read as good or catastrophic.
            metrics.record_storage(operation="upload", success=True)
            return True
        events.storage_error(operation="upload", key=key, detail=detail)
        metrics.record_storage(operation="upload", success=False)
        job.status = JobStatus.failed
        job.error = f"STORAGE_UPLOAD_FAILED: {detail}"
        job.error_code = "STORAGE_UPLOAD_FAILED"
        job.retryable = True
        await self.db.flush()
        return False

    async def enqueue_images(
        self,
        organization,
        *,
        client_id: UUID,
        prompt: str,
        campaign_id: UUID | None = None,
        aspect_ratio: str = "1:1",
        quantity: int = 1,
        platform: str | None = None,
        idempotency_key: str | None = None,
        concept_id: UUID | None = None,
        variation_id: UUID | None = None,
        run_id: UUID | None = None,
        max_quantity: int = 5,
    ) -> dict:
        provider = get_image_provider()
        if not provider.configured():
            # NOT_CONFIGURED, not FAILED. A missing provider is a setup gap the
            # operator can close, and reporting it as a failure sends the reader
            # looking for a broken generation that never ran.
            return {
                "job_id": None,
                "jobs": [],
                "status": "NOT_CONFIGURED",
                "error": "IMAGE GENERATION NOT CONFIGURED",
                "error_code": "MEDIA_PROVIDER_NOT_CONFIGURED",
                "message": "Image generation is not configured. Add IMAGE_PROVIDER and IMAGE_API_KEY (or OPENAI_API_KEY).",
            }

        quantity = max(1, min(int(quantity or 1), max(1, int(max_quantity))))
        jobs_out = []
        for i in range(quantity):
            key = f"{idempotency_key}:{i}" if idempotency_key else None
            if key:
                existing = await self.db.scalar(
                    select(ImageJob).where(
                        ImageJob.organization_id == organization.id,
                        ImageJob.idempotency_key == key,
                    )
                )
                if existing and existing.status == JobStatus.completed:
                    jobs_out.append(await self._image_job_payload(existing))
                    continue

            job = ImageJob(
                organization_id=organization.id,
                client_id=client_id,
                campaign_id=campaign_id,
                concept_id=concept_id,
                variation_id=variation_id,
                run_id=run_id,
                provider=provider.name,
                prompt=prompt if quantity == 1 else f"{prompt} — variation {i + 1}",
                aspect_ratio=aspect_ratio,
                idempotency_key=key,
                status=JobStatus.queued,
            )
            w, h = parse_aspect_ratio(aspect_ratio)
            job.width, job.height = w, h
            self.db.add(job)
            await self.db.flush()
            await self._dispatch(
                organization,
                job_type=IMAGE_GENERATE,
                payload={"image_job_id": str(job.id), "platform": platform},
                dedupe_key=f"image:{job.id}",
                run_inline=lambda: self._process_image_job(organization, job, platform=platform),
            )
            jobs_out.append(await self._image_job_payload(job))

        primary = jobs_out[0] if jobs_out else {"status": "FAILED"}
        return {
            "job_id": primary.get("job_id"),
            "jobs": jobs_out,
            "status": primary.get("status"),
            "provider": primary.get("provider"),
            "prompt": primary.get("prompt"),
            "assets": primary.get("assets") or [],
            "message": primary.get("message"),
            "error": primary.get("error"),
            "error_code": primary.get("error_code"),
            "retryable": primary.get("retryable", False),
            "demo": primary.get("demo", False),
        }

    async def get_image_job(self, organization_id: UUID, job_id: UUID) -> dict:
        job = await self.db.scalar(
            select(ImageJob).where(ImageJob.id == job_id, ImageJob.organization_id == organization_id)
        )
        if not job:
            return {"error": "NOT_FOUND", "status": "FAILED"}
        return await self._image_job_payload(job)

    async def enqueue_video(
        self,
        organization,
        *,
        client_id: UUID,
        prompt: str,
        campaign_id: UUID | None = None,
        duration_seconds: int = 10,
        aspect_ratio: str = "9:16",
        platform: str | None = None,
        idempotency_key: str | None = None,
        concept_id: UUID | None = None,
        variation_id: UUID | None = None,
        run_id: UUID | None = None,
    ) -> dict:
        provider = get_video_provider()
        if not provider.configured():
            return {
                "job_id": None,
                "provider_job_id": None,
                "status": "NOT_CONFIGURED",
                "error": "VIDEO GENERATION NOT CONFIGURED",
                "error_code": "MEDIA_PROVIDER_NOT_CONFIGURED",
                "message": "Video generation is not configured. Add VIDEO_PROVIDER and VIDEO_API_KEY (and VIDEO_MODEL for Replicate).",
            }

        if idempotency_key:
            existing = await self.db.scalar(
                select(VideoJob).where(
                    VideoJob.organization_id == organization.id,
                    VideoJob.idempotency_key == idempotency_key,
                )
            )
            if existing and existing.status == JobStatus.completed:
                return await self._video_job_payload(existing)

        job = VideoJob(
            organization_id=organization.id,
            client_id=client_id,
            campaign_id=campaign_id,
            concept_id=concept_id,
            variation_id=variation_id,
            run_id=run_id,
            provider=provider.name,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            idempotency_key=idempotency_key,
            status=JobStatus.queued,
        )
        self.db.add(job)
        await self.db.flush()
        await self._dispatch(
            organization,
            job_type=VIDEO_GENERATE,
            payload={"video_job_id": str(job.id), "platform": platform},
            dedupe_key=f"video:{job.id}",
            run_inline=lambda: self._process_video_job(
                organization, job, platform=platform, poll=False
            ),
        )
        return await self._video_job_payload(job)

    async def get_video_job(self, organization_id: UUID, job_id: UUID, *, poll: bool = True) -> dict:
        job = await self.db.scalar(
            select(VideoJob).where(VideoJob.id == job_id, VideoJob.organization_id == organization_id)
        )
        if not job:
            return {"error": "NOT_FOUND", "status": "FAILED"}
        if poll and job.status in {JobStatus.submitted, JobStatus.processing, JobStatus.generating} and job.provider_job_id:
            org = await self.db.get(Organization, organization_id)
            if org:
                await self._process_video_job(org, job, poll=True)
        return await self._video_job_payload(job)

    async def cancel_job(self, organization_id: UUID, job_id: UUID, *, kind: str) -> dict:
        """
        Cancel a media job that has not finished.

        For video the provider is asked first, and its answer decides the local
        state. A generation still running at the provider keeps costing money, so
        recording CANCELLED locally after a refused cancellation would produce the
        worst possible state: a job nobody is watching that is still billing. The
        refusal is surfaced as a structured error and the job is left exactly as
        it was, so the truth remains visible and the caller can retry or let the
        generation finish.

        An already-finished job returns without asking the provider — there is
        nothing running to stop, and a needless call could be rejected and read
        as a failure.
        """
        model = ImageJob if kind == "image" else VideoJob
        job = await self.db.scalar(
            select(model).where(model.id == job_id, model.organization_id == organization_id)
        )
        if not job:
            return {"error": "NOT_FOUND", "status": "FAILED"}
        if job.status in {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}:
            payload = await self._job_payload(job, kind)
            payload["message"] = "This job had already finished; nothing to cancel."
            return payload

        provider_message = None
        if kind == "video" and getattr(job, "provider_job_id", None):
            outcome = await get_video_provider().cancel(job.provider_job_id)
            provider_message = outcome.message
            if not outcome.success:
                # Recorded, not hidden: the operator needs to know the provider
                # may still be generating and billing.
                events.media_generation(
                    kind="video",
                    provider=job.provider,
                    job_id=job.id,
                    organization_id=organization_id,
                    status="CANCEL_FAILED",
                    error=outcome.error,
                )
                # Nothing has been written, so there is nothing to roll back and
                # the job keeps its real state. Only the provider's mapped error
                # code travels back — its raw response body is logged, never
                # returned, because a provider body can echo request content.
                raise ProviderError(
                    "The provider refused to cancel this generation, so it may still be "
                    "running. The job has been left in its current state.",
                    code="MEDIA_CANCELLATION_FAILED",
                    details={
                        "job_status": _api_status(job.status),
                        "provider": job.provider,
                        "provider_error_code": outcome.error_code or "CANCEL_REFUSED",
                    },
                )

        job.status = JobStatus.cancelled
        job.error = None
        job.error_code = None
        job.retryable = False
        job.result = {**(job.result or {}), "message": provider_message or "Cancelled."}
        flag_modified(job, "result")
        await self.db.flush()
        return await self._job_payload(job, kind)

    async def _job_payload(self, job, kind: str) -> dict:
        return (
            await self._image_job_payload(job)
            if kind == "image"
            else await self._video_job_payload(job)
        )

    async def create_variations(
        self,
        organization,
        *,
        asset_id: UUID,
        count: int = 3,
        user_id: UUID | None = None,
    ) -> dict:
        asset = await self.db.scalar(
            select(CreativeAsset).where(
                CreativeAsset.id == asset_id,
                CreativeAsset.organization_id == organization.id,
            )
        )
        if not asset:
            return {"error": "NOT_FOUND"}
        prompt = asset.prompt or (asset.content or {}).get("prompt") or asset.name
        if asset.asset_type in {"video", "video_concept"}:
            jobs = []
            for i in range(max(1, min(count, 4))):
                result = await self.enqueue_video(
                    organization,
                    client_id=asset.client_id,
                    campaign_id=asset.campaign_id,
                    prompt=f"{prompt} — variation {i + 1}",
                    aspect_ratio=(asset.meta or {}).get("aspect_ratio") or "9:16",
                    idempotency_key=f"var:{asset.id}:{i}:{uuid4().hex[:8]}",
                )
                jobs.append(result)
            return {"kind": "video", "jobs": jobs}
        results = await self.enqueue_images(
            organization,
            client_id=asset.client_id,
            campaign_id=asset.campaign_id,
            prompt=str(prompt),
            aspect_ratio=(asset.meta or {}).get("aspect_ratio") or "1:1",
            quantity=count,
            idempotency_key=f"var:{asset.id}:{uuid4().hex[:8]}",
        )
        return {"kind": "image", **results}

    async def _process_image_job(self, organization, job: ImageJob, *, platform: str | None = None) -> None:
        provider = get_image_provider()
        job.attempts = (job.attempts or 0) + 1
        job.status = JobStatus.generating
        await self.db.flush()

        result = await provider.generate_image(
            prompt=job.prompt,
            width=job.width or 1024,
            height=job.height or 1024,
            meta={"aspect_ratio": job.aspect_ratio, "job_id": str(job.id)},
        )
        if not result.success or not result.media_bytes:
            job.status = JobStatus.failed
            job.error = result.error or result.message
            job.error_code = result.error_code
            job.retryable = bool(result.retryable)
            job.result = {
                "message": result.message,
                "demo": result.demo,
                "assets": result.assets,
            }
            await self.db.flush()
            events.media_generation(
                kind="image",
                provider=provider.name,
                job_id=job.id,
                organization_id=organization.id,
                status="FAILED",
                error=job.error,
            )
            metrics.record_media(kind="image", provider=provider.name, status="failed")
            return

        if not is_valid_image(result.media_bytes):
            job.status = JobStatus.failed
            job.error = "INVALID_IMAGE"
            job.error_code = "INVALID_IMAGE"
            job.retryable = True
            await self.db.flush()
            return

        job.status = JobStatus.uploading
        await self.db.flush()

        mime = result.mime_type or detect_image_mime(result.media_bytes) or "image/png"
        ext = "png" if "png" in mime else "jpg"
        key = (
            f"organizations/{organization.id}/clients/{job.client_id}/"
            f"campaigns/{job.campaign_id or 'none'}/images/{job.id}.{ext}"
        )
        if not await self._persist(job, result.media_bytes, key, mime):
            return

        demo = bool(result.demo or effective_demo_mode(organization))
        asset = CreativeAsset(
            organization_id=organization.id,
            client_id=job.client_id,
            campaign_id=job.campaign_id,
            concept_id=job.concept_id,
            variation_id=job.variation_id,
            name=(job.prompt[:80] or "Generated image"),
            asset_type="image",
            aspect_ratio=job.aspect_ratio,
            platform=platform,
            prompt=job.prompt,
            provider=provider.name,
            model=get_settings().image_model if provider.name == "openai" else provider.name,
            storage_key=key,
            mime_type=mime,
            width=result.width or job.width,
            height=result.height or job.height,
            provider_asset_id=result.external_id,
            status="completed",
            content={"note": "DEMO" if result.demo else "live_image"},
            meta={"aspect_ratio": job.aspect_ratio, "job_id": str(job.id), "demo": result.demo},
            data_source="demo" if demo else "live",
        )
        self.db.add(asset)
        await self.db.flush()

        job.creative_asset_id = asset.id
        job.status = JobStatus.completed
        job.error = None
        job.error_code = None
        job.retryable = False
        job.result = {
            "message": result.message,
            "demo": result.demo,
            "asset_id": str(asset.id),
            "storage_key": key,
            "mime_type": mime,
            "url": f"/api/v1/creative/media/{asset.id}",
        }
        flag_modified(job, "result")
        await self.db.flush()
        events.media_generation(
            kind="image",
            provider=provider.name,
            job_id=job.id,
            organization_id=organization.id,
            status="COMPLETED",
        )
        metrics.record_media(kind="image", provider=provider.name, status="completed")
        # Keyed on the asset, so a retried job that re-persists the same asset
        # does not bill twice.
        await meter(
            self.db,
            organization_id=organization.id,
            metric=Metric.IMAGE_GENERATION,
            idempotency_key=f"image:{asset.id}",
            client_id=getattr(job, "client_id", None),
            details={"provider": provider.name, "job_id": str(job.id)},
        )
        await meter(
            self.db,
            organization_id=organization.id,
            metric=Metric.STORAGE_BYTES,
            quantity=len(result.media_bytes or b""),
            idempotency_key=f"storage:{asset.id}",
            client_id=getattr(job, "client_id", None),
            details={"storage_key": key},
        )

    async def _process_video_job(
        self, organization, job: VideoJob, *, platform: str | None = None, poll: bool = False
    ) -> None:
        provider = get_video_provider()
        job.attempts = (job.attempts or 0) + 1

        if poll and job.provider_job_id:
            job.status = JobStatus.processing
            await self.db.flush()
            result = await provider.get_status(job.provider_job_id)
        else:
            job.status = JobStatus.submitted
            await self.db.flush()
            result = await provider.generate_video(
                prompt=job.prompt,
                duration_seconds=job.duration_seconds or 10,
                aspect_ratio=job.aspect_ratio or "9:16",
                meta={"job_id": str(job.id)},
            )
            if result.external_id:
                job.provider_job_id = result.external_id

        if result.status in {"submitted", "processing", "generating"} and not result.media_bytes:
            # Return immediately. Completion is detected by the `media.poll_video`
            # job; blocking here used to hold the caller for up to ~62 seconds and
            # then give up, stranding a job that was still running at the provider.
            job.status = JobStatus.processing if result.status == "processing" else JobStatus.submitted
            job.result = {
                "message": result.message or "Submitted to provider; generation in progress.",
                "provider_job_id": job.provider_job_id,
            }
            await self.db.flush()
            return

        if not result.success or not result.media_bytes:
            # Still processing
            if result.status in {"submitted", "processing"}:
                job.status = JobStatus.processing
                job.result = {"message": result.message, "provider_job_id": job.provider_job_id}
                await self.db.flush()
                return
            job.status = JobStatus.failed
            job.error = result.error or result.message
            job.error_code = result.error_code
            job.retryable = bool(result.retryable)
            job.result = {"message": result.message, "demo": result.demo, "assets": result.assets}
            await self.db.flush()
            metrics.record_media(kind="video", provider=provider.name, status="failed")
            return

        if not is_valid_video(result.media_bytes):
            job.status = JobStatus.failed
            job.error = "INVALID_VIDEO"
            job.error_code = "INVALID_VIDEO"
            job.retryable = True
            await self.db.flush()
            # Counted as a failure: bytes that are not a playable video are a
            # failed generation, not a successful one with an odd payload.
            metrics.record_media(kind="video", provider=provider.name, status="failed")
            return

        job.status = JobStatus.downloading
        await self.db.flush()
        job.status = JobStatus.uploading
        await self.db.flush()

        mime = result.mime_type or detect_video_mime(result.media_bytes) or "video/mp4"
        key = (
            f"organizations/{organization.id}/clients/{job.client_id}/"
            f"campaigns/{job.campaign_id or 'none'}/videos/{job.id}.mp4"
        )
        if not await self._persist(job, result.media_bytes, key, mime):
            return

        demo = bool(result.demo or effective_demo_mode(organization))
        asset = CreativeAsset(
            organization_id=organization.id,
            client_id=job.client_id,
            campaign_id=job.campaign_id,
            concept_id=job.concept_id,
            variation_id=job.variation_id,
            name=(job.prompt[:80] or "Generated video"),
            asset_type="video",
            aspect_ratio=job.aspect_ratio,
            platform=platform,
            prompt=job.prompt,
            provider=provider.name,
            model=get_settings().video_model or provider.name,
            storage_key=key,
            mime_type=mime,
            duration_seconds=job.duration_seconds,
            provider_asset_id=job.provider_job_id,
            status="completed",
            content={"note": "DEMO" if result.demo else "live_video"},
            meta={"aspect_ratio": job.aspect_ratio, "job_id": str(job.id), "demo": result.demo},
            data_source="demo" if demo else "live",
        )
        self.db.add(asset)
        await self.db.flush()
        job.creative_asset_id = asset.id
        job.status = JobStatus.completed
        job.error = None
        job.error_code = None
        job.result = {
            "message": result.message,
            "demo": result.demo,
            "asset_id": str(asset.id),
            "storage_key": key,
            "mime_type": mime,
            "url": f"/api/v1/creative/media/{asset.id}",
            "provider_job_id": job.provider_job_id,
        }
        flag_modified(job, "result")
        await self.db.flush()
        events.media_generation(
            kind="video",
            provider=provider.name,
            job_id=job.id,
            organization_id=organization.id,
            status="COMPLETED",
        )
        metrics.record_media(kind="video", provider=provider.name, status="completed")
        # Keyed on the asset, so a retried job that re-persists the same asset
        # does not bill twice.
        await meter(
            self.db,
            organization_id=organization.id,
            metric=Metric.VIDEO_GENERATION,
            idempotency_key=f"video:{asset.id}",
            client_id=getattr(job, "client_id", None),
            details={"provider": provider.name, "job_id": str(job.id)},
        )
        await meter(
            self.db,
            organization_id=organization.id,
            metric=Metric.STORAGE_BYTES,
            quantity=len(result.media_bytes or b""),
            idempotency_key=f"storage:{asset.id}",
            client_id=getattr(job, "client_id", None),
            details={"storage_key": key},
        )

    async def _asset_bytes_present(self, key: str) -> bool:
        """A storage outage must not be reported as a missing file."""
        try:
            return await self.storage.exists(key)
        except StorageError:
            return True

    async def _image_job_payload(self, job: ImageJob) -> dict:
        assets = []
        if job.creative_asset_id and job.status == JobStatus.completed:
            asset = await self.db.get(CreativeAsset, job.creative_asset_id)
            if asset and asset.storage_key and await self._asset_bytes_present(asset.storage_key):
                assets.append(
                    {
                        "id": str(asset.id),
                        "url": f"/api/v1/creative/media/{asset.id}",
                        "mime_type": asset.mime_type or "image/png",
                        "width": asset.width,
                        "height": asset.height,
                        "demo": asset.data_source == "demo",
                    }
                )
            elif job.status == JobStatus.completed:
                # Completed without file is invalid — correct status
                job.status = JobStatus.failed
                job.error = "COMPLETED_WITHOUT_FILE"
                await self.db.flush()
        return {
            "job_id": str(job.id),
            "status": _api_status(job.status),
            "provider": job.provider,
            "prompt": job.prompt,
            "assets": assets,
            "error": job.error,
            "error_code": job.error_code,
            "retryable": job.retryable,
            "message": (job.result or {}).get("message"),
            "demo": (job.result or {}).get("demo", False),
        }

    async def _video_job_payload(self, job: VideoJob) -> dict:
        assets = []
        if job.creative_asset_id and job.status == JobStatus.completed:
            asset = await self.db.get(CreativeAsset, job.creative_asset_id)
            if asset and asset.storage_key and await self._asset_bytes_present(asset.storage_key):
                assets.append(
                    {
                        "id": str(asset.id),
                        "url": f"/api/v1/creative/media/{asset.id}",
                        "mime_type": asset.mime_type or "video/mp4",
                        "duration_seconds": asset.duration_seconds,
                        "demo": asset.data_source == "demo",
                    }
                )
            elif job.status == JobStatus.completed:
                job.status = JobStatus.failed
                job.error = "COMPLETED_WITHOUT_FILE"
                await self.db.flush()
        return {
            "job_id": str(job.id),
            "provider_job_id": job.provider_job_id,
            "status": _api_status(job.status),
            "provider": job.provider,
            "prompt": job.prompt,
            "assets": assets,
            "error": job.error,
            "error_code": job.error_code,
            "retryable": job.retryable,
            "message": (job.result or {}).get("message"),
            "demo": (job.result or {}).get("demo", False),
        }
