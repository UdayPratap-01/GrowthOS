"""
P2-A — the real media pipeline behind campaign generation.

These tests are about the invariant the whole feature rests on: a job is only
COMPLETED when real bytes from a provider are in storage and can be read back.
Every way that can go wrong is covered here — the provider fails, the provider
returns something that is not an image, storage refuses the upload, the job is
delivered twice, the video is asynchronous and has to be polled — and in each
case the job must end up in a state that tells the truth.

Providers are substituted with fakes that behave like the real thing (async
submission, provider job ids, real MP4 header bytes). That is deliberately not
the same claim as "a commercial provider works": only the real-provider
verification run can support that.
"""

from __future__ import annotations

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

os.environ["DEMO_MODE"] = "true"
os.environ["IMAGE_PROVIDER"] = "demo"
os.environ["VIDEO_PROVIDER"] = "none"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["STORAGE_LOCAL_PATH"] = "./storage_test_pipeline"

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.core.security import hash_password  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.generation.base import (  # noqa: E402
    GenerationResult,
    ImageGenerationProvider,
    VideoGenerationProvider,
)
from app.generation.media_utils import make_demo_png  # noqa: E402
from app.main import app  # noqa: E402
from app.models.automation import ImageJob, VideoJob  # noqa: E402
from app.models.client import Client  # noqa: E402
from app.models.enums import JobStatus, MemberRole  # noqa: E402
from app.models.organization import Organization, OrganizationMember  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.media_generation_service import MediaGenerationService  # noqa: E402
from app.services.usage_service import Metric, UsageService  # noqa: E402
from app.storage import StorageError, set_object_storage  # noqa: E402

PASSWORD = "Str0ng-Test-Passw0rd!"

#: Minimal bytes that pass `is_valid_video`: an ISO-BMFF ftyp box and enough
#: payload to be non-trivial. Stands in for a provider download.
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 512


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FailingImageProvider(ImageGenerationProvider):
    name = "failing"

    def configured(self) -> bool:
        return True

    async def generate_image(self, *, prompt, width=1024, height=1024, meta=None):
        return GenerationResult(
            success=False,
            status="failed",
            provider=self.name,
            message="Provider rejected the prompt.",
            error="Content policy violation",
            error_code="CONTENT_POLICY",
            retryable=False,
        )

    async def generate_variations(self, *, prompt, count=3, meta=None):
        return await self.generate_image(prompt=prompt)

    async def get_status(self, job_id: str):
        return await self.generate_image(prompt="")


class GarbageImageProvider(ImageGenerationProvider):
    """Returns success with bytes that are not an image — the subtler failure."""

    name = "garbage"

    def configured(self) -> bool:
        return True

    async def generate_image(self, *, prompt, width=1024, height=1024, meta=None):
        return GenerationResult(
            success=True,
            status="completed",
            provider=self.name,
            message="ok",
            media_bytes=b"<html>rate limited</html>",
            mime_type="image/png",
        )

    async def generate_variations(self, *, prompt, count=3, meta=None):
        return await self.generate_image(prompt=prompt)

    async def get_status(self, job_id: str):
        return await self.generate_image(prompt="")


class AsyncVideoProvider(VideoGenerationProvider):
    """
    Submits, reports processing once, then returns a real MP4 — the shape every
    commercial video provider has.
    """

    name = "fake-async-video"

    def __init__(self) -> None:
        self.polls = 0
        self.cancelled: list[str] = []

    def configured(self) -> bool:
        return True

    async def generate_video(self, *, prompt, duration_seconds=10, aspect_ratio="9:16", meta=None):
        return GenerationResult(
            success=True,
            status="submitted",
            provider=self.name,
            message="Submitted to provider.",
            external_id="prov-job-123",
        )

    async def generate_variation(self, *, prompt, meta=None):
        return await self.generate_video(prompt=prompt)

    async def get_status(self, provider_job_id: str):
        self.polls += 1
        if self.polls < 2:
            return GenerationResult(
                success=True,
                status="processing",
                provider=self.name,
                message="Still rendering.",
                external_id=provider_job_id,
            )
        return GenerationResult(
            success=True,
            status="completed",
            provider=self.name,
            message="Done.",
            external_id=provider_job_id,
            media_bytes=FAKE_MP4,
            mime_type="video/mp4",
        )

    async def get_result(self, provider_job_id: str):
        return await self.get_status(provider_job_id)

    async def cancel(self, provider_job_id: str):
        self.cancelled.append(provider_job_id)
        return GenerationResult(
            success=True,
            status="cancelled",
            provider=self.name,
            message="Provider confirmed cancellation.",
            external_id=provider_job_id,
        )


class RefusingStorage:
    """Accepts nothing. Models an outage or a bad bucket policy."""

    backend = "refusing"

    async def upload(self, data: bytes, key: str, content_type: str) -> str:
        raise StorageError("bucket is not writable")

    async def exists(self, key: str) -> bool:
        return False

    async def get_bytes(self, key: str) -> bytes | None:
        return None

    async def delete(self, key: str) -> None:
        return None

    def public_url(self, key: str) -> str | None:
        return None


class SilentlyEmptyStorage:
    """
    Reports a successful upload but has nothing afterwards.

    The nastiest storage failure, and the one that would otherwise produce a
    COMPLETED job pointing at a file that does not exist.
    """

    backend = "silent"

    async def upload(self, data: bytes, key: str, content_type: str) -> str:
        return key

    async def exists(self, key: str) -> bool:
        return False

    async def get_bytes(self, key: str) -> bytes | None:
        return None

    async def delete(self, key: str) -> None:
        return None

    def public_url(self, key: str) -> str | None:
        return None


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


async def _org_and_client() -> tuple[str, uuid.UUID, uuid.UUID]:
    suffix = uuid.uuid4().hex[:8]
    email = f"media-{suffix}@p2atest.com"
    async with AsyncSessionLocal() as db:
        user = User(email=email, hashed_password=hash_password(PASSWORD), full_name="Media tester")
        org = Organization(name=f"Media {suffix}", slug=f"media-{suffix}", demo_mode=True)
        db.add_all([user, org])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        client = Client(organization_id=org.id, business_name="Media Co", industry="retail")
        db.add(client)
        await db.commit()
        return email, org.id, client.id


async def _login(http: AsyncClient, email: str) -> dict[str, str]:
    resp = await http.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _generate_one_image(monkeypatch, provider, *, storage=None) -> tuple[ImageJob, uuid.UUID]:
    """Run one image job end to end with the given provider, inline."""
    email, org_id, client_id = await _org_and_client()
    monkeypatch.setattr("app.services.media_generation_service.get_image_provider", lambda: provider)
    if storage is not None:
        set_object_storage(storage)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        headers = await _login(http, email)
        resp = await http.post(
            "/api/v1/creative/images/generate",
            headers=headers,
            json={"client_id": str(client_id), "prompt": "a dentist's chair at golden hour"},
        )
    assert resp.status_code == 200, resp.text

    async with AsyncSessionLocal() as db:
        job = await db.scalar(select(ImageJob).where(ImageJob.organization_id == org_id))
    assert job is not None
    return job, org_id


# --------------------------------------------------------------------------
# Image failures
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_failure_is_recorded_and_never_becomes_completed(monkeypatch):
    job, org_id = await _generate_one_image(monkeypatch, FailingImageProvider())

    assert job.status == JobStatus.failed
    assert job.error_code == "CONTENT_POLICY"
    assert job.creative_asset_id is None
    async with AsyncSessionLocal() as db:
        # A failed generation must not be billed as an image.
        assert await UsageService(db).total(org_id, Metric.IMAGE_GENERATION) == 0


@pytest.mark.asyncio
async def test_bytes_that_are_not_an_image_are_rejected(monkeypatch):
    job, org_id = await _generate_one_image(monkeypatch, GarbageImageProvider())

    assert job.status == JobStatus.failed
    assert job.error_code == "INVALID_IMAGE"
    assert job.creative_asset_id is None
    # Retryable: a provider returning an error page is usually transient.
    assert job.retryable is True


@pytest.mark.asyncio
async def test_storage_failure_fails_the_job_rather_than_claiming_success(monkeypatch):
    from app.generation.image import DemoImageProvider

    job, org_id = await _generate_one_image(
        monkeypatch, DemoImageProvider(), storage=RefusingStorage()
    )

    assert job.status == JobStatus.failed
    assert job.error_code == "STORAGE_UPLOAD_FAILED"
    assert job.creative_asset_id is None
    assert job.retryable is True
    async with AsyncSessionLocal() as db:
        assert await UsageService(db).total(org_id, Metric.IMAGE_GENERATION) == 0


@pytest.mark.asyncio
async def test_upload_that_cannot_be_read_back_is_not_completed(monkeypatch):
    """The invariant: COMPLETED requires the stored object to actually be there."""
    from app.generation.image import DemoImageProvider

    job, _org_id = await _generate_one_image(
        monkeypatch, DemoImageProvider(), storage=SilentlyEmptyStorage()
    )

    assert job.status == JobStatus.failed
    assert job.error_code == "STORAGE_UPLOAD_FAILED"


# --------------------------------------------------------------------------
# Job delivery semantics
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_job_delivery_does_not_generate_twice(monkeypatch):
    """A queue that delivers twice must not produce a second paid asset."""
    from app.generation.image import DemoImageProvider
    from app.jobs.handlers import handle_generate_image
    from app.models.automation import BackgroundJob

    job, org_id = await _generate_one_image(monkeypatch, DemoImageProvider())
    assert job.status == JobStatus.completed

    async with AsyncSessionLocal() as db:
        replay = BackgroundJob(
            organization_id=org_id,
            job_type="media.generate_image",
            status=JobStatus.running,
            payload={"image_job_id": str(job.id)},
        )
        db.add(replay)
        await db.flush()
        result = await handle_generate_image(db, replay)
        await db.commit()

    assert result["duplicate"] is True
    async with AsyncSessionLocal() as db:
        assert await UsageService(db).total(org_id, Metric.IMAGE_GENERATION) == 1


@pytest.mark.asyncio
async def test_a_job_cannot_be_run_for_another_tenant(monkeypatch):
    """A guessed or forged payload id must not let a worker touch another tenant."""
    from app.generation.image import DemoImageProvider
    from app.jobs.handlers import UnrecoverableJobError, handle_generate_image
    from app.models.automation import BackgroundJob

    job, _org_id = await _generate_one_image(monkeypatch, DemoImageProvider())
    _email, other_org_id, _client_id = await _org_and_client()

    async with AsyncSessionLocal() as db:
        forged = BackgroundJob(
            organization_id=other_org_id,
            job_type="media.generate_image",
            status=JobStatus.running,
            payload={"image_job_id": str(job.id)},
        )
        db.add(forged)
        await db.flush()
        with pytest.raises(UnrecoverableJobError):
            await handle_generate_image(db, forged)


@pytest.mark.asyncio
async def test_repeated_idempotency_key_reuses_the_completed_job(monkeypatch):
    from app.generation.image import DemoImageProvider

    email, org_id, client_id = await _org_and_client()
    monkeypatch.setattr(
        "app.services.media_generation_service.get_image_provider", lambda: DemoImageProvider()
    )
    key = f"batch-{uuid.uuid4().hex[:6]}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        headers = await _login(http, email)
        body = {"client_id": str(client_id), "prompt": "storefront", "idempotency_key": key}
        first = await http.post("/api/v1/creative/images/generate", headers=headers, json=body)
        second = await http.post("/api/v1/creative/images/generate", headers=headers, json=body)

    assert first.json()["job_id"] == second.json()["job_id"]
    async with AsyncSessionLocal() as db:
        assert await UsageService(db).total(org_id, Metric.IMAGE_GENERATION) == 1


# --------------------------------------------------------------------------
# Video: async submit → poll → complete
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_video_is_submitted_then_polled_and_only_then_completed(monkeypatch):
    provider = AsyncVideoProvider()
    monkeypatch.setattr(
        "app.services.media_generation_service.get_video_provider", lambda: provider
    )
    email, org_id, client_id = await _org_and_client()

    async with AsyncSessionLocal() as db:
        organization = await db.get(Organization, org_id)
        service = MediaGenerationService(db)
        submitted = await service.enqueue_video(
            organization,
            client_id=client_id,
            prompt="a slow pan across a clinic reception",
            aspect_ratio="9:16",
        )
        await db.commit()

    # The HTTP-equivalent call returns as soon as the provider has the job. It
    # must not block for the minutes a real render takes.
    assert submitted["status"] in {"SUBMITTED", "PROCESSING"}
    assert submitted["provider_job_id"] == "prov-job-123"
    assert not submitted["assets"]

    job_id = uuid.UUID(submitted["job_id"])
    async with AsyncSessionLocal() as db:
        service = MediaGenerationService(db)
        # First poll: provider still working, so still no asset.
        interim = await service.get_video_job(org_id, job_id, poll=True)
        assert interim["status"] == "PROCESSING"
        assert not interim["assets"]

        final = await service.get_video_job(org_id, job_id, poll=True)
        await db.commit()

    assert final["status"] == "COMPLETED"
    assert final["assets"], "COMPLETED requires a stored asset"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        headers = await _login(http, email)
        fetched = await http.get(final["assets"][0]["url"], headers=headers)
    assert fetched.status_code == 200
    assert fetched.content == FAKE_MP4, "the stored object must be the provider's bytes"
    assert fetched.headers["content-type"].startswith("video/")

    async with AsyncSessionLocal() as db:
        assert await UsageService(db).total(org_id, Metric.VIDEO_GENERATION) == 1


@pytest.mark.asyncio
async def test_cancelling_a_video_asks_the_provider_to_stop(monkeypatch):
    provider = AsyncVideoProvider()
    monkeypatch.setattr(
        "app.services.media_generation_service.get_video_provider", lambda: provider
    )
    email, org_id, client_id = await _org_and_client()

    async with AsyncSessionLocal() as db:
        organization = await db.get(Organization, org_id)
        submitted = await MediaGenerationService(db).enqueue_video(
            organization, client_id=client_id, prompt="a clinic tour"
        )
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        headers = await _login(http, email)
        cancelled = await http.post(
            f"/api/v1/creative/videos/jobs/{submitted['job_id']}/cancel", headers=headers
        )

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"
    # The point of cancelling is that the provider stops billing.
    assert provider.cancelled == ["prov-job-123"]

    async with AsyncSessionLocal() as db:
        job = await db.get(VideoJob, uuid.UUID(submitted["job_id"]))
        assert job.status == JobStatus.cancelled
        assert await UsageService(db).total(org_id, Metric.VIDEO_GENERATION) == 0


@pytest.mark.asyncio
async def test_cancelling_a_finished_job_is_reported_not_pretended(monkeypatch):
    from app.generation.image import DemoImageProvider

    job, org_id = await _generate_one_image(monkeypatch, DemoImageProvider())
    assert job.status == JobStatus.completed

    async with AsyncSessionLocal() as db:
        result = await MediaGenerationService(db).cancel_job(org_id, job.id, kind="image")

    assert result["status"] == "COMPLETED"
    assert "already finished" in result["message"]


@pytest.mark.asyncio
async def test_a_provider_without_cancel_support_says_so():
    """An honest refusal beats a cancellation the provider never performed."""
    from app.generation.image import DemoImageProvider

    outcome = await DemoImageProvider().cancel("anything")
    assert outcome.success is False
    assert outcome.error_code == "CANCEL_NOT_SUPPORTED"


# --------------------------------------------------------------------------
# Campaign-level media wiring
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_campaign_media_jobs_carry_their_concept_and_run(monkeypatch):
    """
    The link that makes the library and the preview work.

    Without concept_id and run_id on the job, progress could not be counted per
    run and an asset could not be shown against the idea it illustrates.
    """
    from app.generation.image import DemoImageProvider

    monkeypatch.setattr(
        "app.services.media_generation_service.get_image_provider", lambda: DemoImageProvider()
    )
    email, org_id, client_id = await _org_and_client()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        headers = await _login(http, email)
        run = await http.post(
            "/api/v1/campaign-generation/generate",
            headers=headers,
            json={
                "client_id": str(client_id),
                "platform": "meta",
                "objective": "traffic",
                "image_quantity": 2,
                "concept_quantity": 2,
            },
        )
        assert run.status_code == 202, run.text
        run_id = run.json()["id"]

        assets = await http.get(
            f"/api/v1/creative/assets?client_id={client_id}&asset_type=image", headers=headers
        )

    async with AsyncSessionLocal() as db:
        jobs = list(await db.scalars(select(ImageJob).where(ImageJob.organization_id == org_id)))

    assert len(jobs) == 2
    assert all(str(job.run_id) == run_id for job in jobs)
    assert all(job.concept_id is not None for job in jobs)
    # Two images over two concepts is one each, not two of the first.
    assert len({job.concept_id for job in jobs}) == 2
    assert all(job.campaign_id is not None for job in jobs)

    library = assets.json()
    assert len(library) == 2
    assert all(item["concept_id"] for item in library)
    assert all(item["campaign_id"] for item in library)
    assert all(item["url"] for item in library)
    # Demo output must be labelled as such, never presented as a real asset.
    assert all(item["is_real"] is False for item in library)
