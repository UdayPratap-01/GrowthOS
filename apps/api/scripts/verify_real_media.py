#!/usr/bin/env python
"""
P2-A-1 — verification of the *real* media provider boundary.

`verify_p2a_e2e.py` already proves the campaign pipeline end to end. It cannot
prove the one link that only a vendor can prove: that `OpenAIImageProvider` and
`ReplicateVideoProvider` form a request the vendor accepts and turn the vendor's
answer into stored bytes. This script targets exactly that boundary.

It runs in one of two modes per medium, chosen by what credentials exist:

  VENDOR   A real key is present. Requests go to the real vendor over the
           network. Every outbound call is recorded, so "the provider was
           actually called" is evidence rather than an assumption. Deliberately
           generates ONE image and ONE video — the minimum that proves the
           chain — because every call costs money.

  STANDIN  No key is present. The adapter is driven against a local stand-in
           that speaks the vendor's documented response shape, through the
           adapter's own httpx code path. This verifies the adapter, the
           validation, storage, the asset record and the REAL/DEMO
           classification. It verifies NOTHING about the vendor, and the report
           says so: the vendor round trip is reported NOT_CONFIGURED.

The assertions are identical in both modes; only the transport differs. That is
the point — the checks below are executed here, so a later run with a real key
is a transport swap and not a new, unexercised code path.

    python scripts/verify_real_media.py

Exit codes: 0 all checks passed · 1 a check failed · 2 could not run
            3 passed, but at least one vendor round trip was NOT_CONFIGURED
"""

from __future__ import annotations

import asyncio
import base64
import os
import struct
import sys
import uuid
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Defaults only when this file is the entrypoint. Importing the helpers from
# tests must not mutate the process environment (e.g. INLINE_JOB_EXECUTION).
if __name__ == "__main__":
    os.environ.setdefault("DEMO_MODE", "true")
    os.environ.setdefault("STORAGE_BACKEND", "local")
    os.environ.setdefault("STORAGE_LOCAL_PATH", "./storage_verify_real_media")
    # Forced off so media runs through the queue and the worker, as in production.
    os.environ.setdefault("INLINE_JOB_EXECUTION", "false")

import httpx  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.generation import image as image_factory  # noqa: E402
from app.generation import openai_image, replicate_video  # noqa: E402
from app.generation import video as video_factory  # noqa: E402
from app.main import app  # noqa: E402
from app.models.client import Client  # noqa: E402
from app.models.enums import MemberRole  # noqa: E402
from app.models.organization import Organization, OrganizationMember  # noqa: E402
from app.models.user import User  # noqa: E402
from app.worker import Worker  # noqa: E402

PASSWORD = "Verify-P2A1-Passw0rd!"
STANDIN_KEY = "standin-not-a-credential"

OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"
REPLICATE_PREDICTIONS = "https://api.replicate.com/v1/predictions"


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


@dataclass
class Report:
    checks: list[tuple[bool, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    not_configured: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    def check(self, ok: bool, description: str) -> bool:
        self.checks.append((bool(ok), description))
        print(f"  {'PASS' if ok else 'FAIL'}  {description}")
        return bool(ok)

    def note(self, text: str) -> None:
        self.notes.append(text)
        print(f"  note  {text}")

    def unconfigured(self, text: str) -> None:
        self.not_configured.append(text)
        print(f"  NOT_CONFIGURED  {text}")

    def finding(self, text: str) -> None:
        self.findings.append(text)
        print(f"  FINDING  {text}")

    @property
    def failed(self) -> list[str]:
        return [text for ok, text in self.checks if not ok]


# ---------------------------------------------------------------------------
# Transport: records every outbound call, then either delegates to the real
# network or answers from the stand-in.
# ---------------------------------------------------------------------------


@dataclass
class Call:
    method: str
    url: str
    auth_scheme: str | None
    status: int | None = None


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.calls: list[Call] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization")
        # Only the scheme is recorded. The credential itself is never read,
        # stored or printed by this script.
        call = Call(request.method, str(request.url), auth.split(" ", 1)[0] if auth else None)
        self.calls.append(call)
        response = await self.inner.handle_async_request(request)
        call.status = response.status_code
        return response

    def to(self, host: str) -> list[Call]:
        return [c for c in self.calls if host in c.url]


@contextmanager
def vendor_transport(recorder: RecordingTransport):
    """
    Point only the two vendor adapters at `recorder`.

    Patching the adapters' module-level `httpx` reference rather than httpx
    itself keeps the ASGI client this script uses for its own API calls
    untouched, so an intercepted vendor call cannot be confused with a request
    to our own API.
    """

    class Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("transport", recorder)
            super().__init__(*args, **kwargs)

    class Shim:
        AsyncClient = Client
        TimeoutException = httpx.TimeoutException
        HTTPError = httpx.HTTPError

    originals = [(openai_image, openai_image.httpx), (replicate_video, replicate_video.httpx)]
    for module, _ in originals:
        module.httpx = Shim
    try:
        yield
    finally:
        for module, original in originals:
            module.httpx = original


# ---------------------------------------------------------------------------
# Stand-in vendor. Speaks the documented response shapes; nothing more.
# ---------------------------------------------------------------------------


def real_png(width: int = 96, height: int = 96) -> bytes:
    """A genuine PNG, built here rather than copied from the demo provider."""
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row.extend(((x * 2) % 256, (y * 2) % 256, 120))
        rows.append(bytes(row))
    compressed = zlib.compress(b"".join(rows), 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")


def iso_mp4() -> bytes:
    """
    Bytes that satisfy the ISO-BMFF container check the pipeline applies.

    Honest about what this is: `detect_video_mime` reads the `ftyp` box, so this
    proves the container-signature path. It is not a playable movie and no claim
    about decodability is made anywhere in this script.
    """
    ftyp = struct.pack(">I", 24) + b"ftypisom" + b"isomiso2avc1mp41"
    free = struct.pack(">I", 8) + b"free"
    mdat = struct.pack(">I", 8 + 256) + b"mdat" + bytes(256)
    return ftyp + free + mdat


class StandInVendor:
    """Minimal OpenAI Images + Replicate predictions surface."""

    def __init__(self) -> None:
        self.polls = 0
        self.cancelled: list[str] = []
        self.cancel_status = 200
        self.image_status = 200
        self.prediction_id = f"standin-{uuid.uuid4().hex[:12]}"
        #: polls before the prediction reports success, so the polling path is
        #: actually traversed instead of completing on submission.
        self.polls_before_success = 1

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == OPENAI_IMAGES_URL:
            return self._image(request)
        if url.startswith(REPLICATE_PREDICTIONS) or "/v1/models/" in url:
            if url.endswith("/cancel"):
                return self._cancel(url)
            if request.method == "GET":
                return self._poll()
            return self._submit()
        if url.startswith("https://standin.invalid/"):
            return httpx.Response(200, content=iso_mp4())
        return httpx.Response(404, json={"error": "stand-in has no route for this url"})

    def _image(self, request: httpx.Request) -> httpx.Response:
        if self.image_status != 200:
            return httpx.Response(self.image_status, json={"error": {"message": "stand-in refusal"}})
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(real_png()).decode()}]},
        )

    def _submit(self) -> httpx.Response:
        return httpx.Response(201, json={"id": self.prediction_id, "status": "starting"})

    def _poll(self) -> httpx.Response:
        self.polls += 1
        if self.polls <= self.polls_before_success:
            return httpx.Response(200, json={"id": self.prediction_id, "status": "processing"})
        return httpx.Response(
            200,
            json={
                "id": self.prediction_id,
                "status": "succeeded",
                "output": "https://standin.invalid/out.mp4",
            },
        )

    def _cancel(self, url: str) -> httpx.Response:
        self.cancelled.append(url)
        if self.cancel_status >= 400:
            return httpx.Response(self.cancel_status, json={"detail": "cannot cancel"})
        return httpx.Response(200, json={"id": self.prediction_id, "status": "canceled"})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def make_tenant(
    label: str, role: MemberRole = MemberRole.owner, *, demo_mode: bool = False
) -> tuple[str, uuid.UUID]:
    """
    Create an organization, a member and a client.

    `demo_mode` matters more than it looks: an asset is labelled demo when the
    provider says so **or** the workspace is in demo mode, so verifying the
    `live` classification requires a workspace that is not pretending.
    """
    suffix = uuid.uuid4().hex[:8]
    email = f"{label}-{suffix}@verify-p2a1.example.com"
    async with AsyncSessionLocal() as db:
        user = User(email=email, hashed_password=hash_password(PASSWORD), full_name="Verifier")
        org = Organization(name=f"Verify {suffix}", slug=f"verify-{suffix}", demo_mode=demo_mode)
        db.add_all([user, org])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=role))
        client = Client(
            organization_id=org.id,
            business_name=f"Verify Client {suffix}",
            industry="home_services",
            website="https://example.com",
            description="A local roofing company serving three suburbs.",
            location="Austin, TX",
            products_services="Roof repair, full replacement, storm damage inspection",
            target_audience="Homeowners aged 35-60 who own their property",
            marketing_goals="Book more inspection appointments",
            brand_voice="Direct, reassuring, no hype",
        )
        db.add(client)
        await db.commit()
        return email, client.id


async def login(http: AsyncClient, email: str) -> dict[str, str]:
    resp = await http.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    resp.raise_for_status()
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def advance_scheduled_jobs() -> int:
    """
    Make jobs scheduled for the future due now.

    Video polling is deliberately spaced out with backoff, so a verification run
    would otherwise finish long before the second poll was due. Only the clock is
    moved — the job, its payload and its handler are untouched, so what runs is
    the real polling path rather than a shortcut around it.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text(
                "UPDATE background_jobs SET run_after = :now "
                "WHERE status = 'queued' AND run_after IS NOT NULL AND run_after > :now"
            ),
            {"now": datetime.now(timezone.utc)},
        )
        await db.commit()
        return result.rowcount or 0


async def drain_queue(*, max_cycles: int = 60, follow_scheduled: bool = True) -> int:
    worker = Worker(batch_size=10, poll_interval=0.0, lease_seconds=120)
    executed = 0
    idle = 0
    for _ in range(max_cycles):
        count = await worker.run_once()
        executed += count
        if count == 0:
            if follow_scheduled and await advance_scheduled_jobs():
                idle = 0
                continue
            idle += 1
            if idle >= 3:
                break
            await asyncio.sleep(0.3)
        else:
            idle = 0
    return executed


def reload_settings(**env: str) -> None:
    for key, value in env.items():
        os.environ[key] = value
    get_settings.cache_clear()


# Env keys that media verification phases may mutate. Phase 3 VENDOR mode must
# see the process environment supplied at invocation, so every stand-in /
# resolution phase snapshots and restores these — including on failure.
MEDIA_ENV_KEYS = (
    "DEMO_MODE",
    "IMAGE_PROVIDER",
    "IMAGE_API_KEY",
    "IMAGE_MODEL",
    "OPENAI_API_KEY",
    "VIDEO_PROVIDER",
    "VIDEO_API_KEY",
    "VIDEO_MODEL",
)


@contextmanager
def preserved_environ(*keys: str):
    """
    Snapshot the named process-env keys and restore them on exit.

    Always clears the settings cache afterward so later phases read the
    restored environment rather than a stale cached Settings object.
    """
    saved = {key: os.environ.get(key) for key in keys}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


@contextmanager
def temporary_settings(**env: str):
    """Apply env overrides for the duration of the block, then restore them."""
    with preserved_environ(*env.keys()):
        reload_settings(**env)
        yield


# ---------------------------------------------------------------------------
# Phase 1 — provider resolution. No credentials required.
# ---------------------------------------------------------------------------


def phase_provider_resolution(report: Report) -> None:
    print("\n1. Provider resolution — production must never fall back to demo")
    with preserved_environ(*MEDIA_ENV_KEYS):
        reload_settings(IMAGE_PROVIDER="openai", IMAGE_API_KEY="", OPENAI_API_KEY="")
        provider = image_factory.get_image_provider()
        report.check(
            provider.name == "openai",
            f"IMAGE_PROVIDER=openai selects the real adapter even with no key (got {provider.name})",
        )
        report.check(
            not provider.configured(),
            "the keyless real adapter reports itself unconfigured rather than usable",
        )

        reload_settings(IMAGE_PROVIDER="demo", DEMO_MODE="false")
        provider = image_factory.get_image_provider()
        report.check(
            provider.name != "demo",
            f"IMAGE_PROVIDER=demo outside demo mode does NOT yield the demo provider (got {provider.name})",
        )

        reload_settings(IMAGE_PROVIDER="some-unknown-vendor", DEMO_MODE="true")
        provider = image_factory.get_image_provider()
        report.check(
            provider.name != "demo" and not provider.configured(),
            f"an unknown image provider is unconfigured, not silently demo (got {provider.name})",
        )

        reload_settings(VIDEO_PROVIDER="replicate", VIDEO_API_KEY="", VIDEO_MODEL="")
        vprovider = video_factory.get_video_provider()
        report.check(
            vprovider.name == "replicate" and not vprovider.configured(),
            "VIDEO_PROVIDER=replicate with no key/model is unconfigured, not demo",
        )

        reload_settings(VIDEO_PROVIDER="demo", DEMO_MODE="false")
        vprovider = video_factory.get_video_provider()
        report.check(
            vprovider.name != "demo",
            f"VIDEO_PROVIDER=demo outside demo mode does NOT yield the demo provider (got {vprovider.name})",
        )


# ---------------------------------------------------------------------------
# Phase 2 — the request the adapter actually forms.
# ---------------------------------------------------------------------------


async def phase_request_shape(report: Report) -> None:
    print("\n2. Outbound request shape — the adapters address the real vendors")
    # Stand-in keys/models must not leak into Phase 3 VENDOR execution.
    with preserved_environ(*MEDIA_ENV_KEYS):
        vendor = StandInVendor()
        recorder = RecordingTransport(httpx.MockTransport(vendor.handler))

        reload_settings(IMAGE_PROVIDER="openai", IMAGE_API_KEY=STANDIN_KEY)
        with vendor_transport(recorder):
            result = await openai_image.OpenAIImageProvider().generate_image(
                prompt="A verification probe for the image adapter", meta={"aspect_ratio": "1:1"}
            )
        image_calls = recorder.to("api.openai.com")
        report.check(bool(image_calls), "the image adapter issued an outbound HTTP request")
        if image_calls:
            report.check(
                image_calls[0].url == OPENAI_IMAGES_URL,
                f"it addressed the real OpenAI Images endpoint ({image_calls[0].url})",
            )
            report.check(
                image_calls[0].auth_scheme == "Bearer",
                f"it authenticated with a Bearer credential (scheme={image_calls[0].auth_scheme})",
            )
        report.check(result.success and result.media_bytes is not None, "the adapter returned bytes")
        report.check(result.demo is False, "bytes from the real adapter are not labelled demo")

        reload_settings(VIDEO_PROVIDER="replicate", VIDEO_API_KEY=STANDIN_KEY, VIDEO_MODEL="owner/model")
        recorder.calls.clear()
        with vendor_transport(recorder):
            submitted = await replicate_video.ReplicateVideoProvider().generate_video(
                prompt="A verification probe for the video adapter", duration_seconds=5
            )
        video_calls = recorder.to("api.replicate.com")
        report.check(bool(video_calls), "the video adapter issued an outbound HTTP request")
        if video_calls:
            report.check(
                "api.replicate.com/v1/models/owner/model/predictions" in video_calls[0].url,
                f"it addressed the real Replicate predictions endpoint ({video_calls[0].url})",
            )
            report.check(
                video_calls[0].auth_scheme == "Token",
                f"it authenticated with a Token credential (scheme={video_calls[0].auth_scheme})",
            )
        report.check(
            bool(submitted.external_id) and submitted.status == "processing",
            f"submission returns a provider job id and a non-terminal status (got {submitted.status})",
        )
        report.check(
            submitted.media_bytes is None,
            "submission alone yields no bytes — nothing is fabricated before the job finishes",
        )


# ---------------------------------------------------------------------------
# Phase 3 — the full chain, campaign to authorized download.
# ---------------------------------------------------------------------------


async def phase_media_chain(report: Report, *, mode: str, recorder: RecordingTransport,
                            vendor: StandInVendor | None, want_video: bool) -> None:
    print(f"\n3. Campaign → concept → job → provider → storage → library  [{mode}]")
    owner_email, client_id = await make_tenant("owner", demo_mode=False)
    other_email, _ = await make_tenant("other", demo_mode=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=180.0) as http:
        owner = await login(http, owner_email)
        other = await login(http, other_email)

        # The transport stays installed for the whole session: reading a run
        # reconciles it, which polls live video jobs at the provider.
        with vendor_transport(recorder):
            started = await http.post(
                "/api/v1/campaign-generation/generate",
                headers=owner,
                json={
                    "client_id": str(client_id),
                    "platform": "meta",
                    "objective": "lead_generation",
                    "offer": "Free storm damage inspection",
                    "daily_budget": "120.00",
                    "currency": "USD",
                    # Prefer a vertical ratio the campaign will hand to video
                    # jobs. Mock concepts often default to 1:1; LTX (and some
                    # other vendors) reject square — keep the fixture
                    # provider-compatible without changing product defaults.
                    "aspect_ratios": ["9:16"],
                    # The minimum that proves the chain. Every unit here is a
                    # real vendor charge in VENDOR mode.
                    "concept_quantity": 1,
                    "image_quantity": 1,
                    "video_quantity": 1 if want_video else 0,
                    "variation_quantity": 0,
                },
            )
            report.check(started.status_code == 202, f"generation accepted asynchronously ({started.status_code})")
            if started.status_code != 202:
                print(started.text)
                return
            run_id = started.json()["id"]

            executed = await drain_queue()
            report.check(executed > 0, f"the worker ran the jobs out of band ({executed} job runs)")

            run = (await http.get(f"/api/v1/campaign-generation/runs/{run_id}", headers=owner)).json()
            campaign_id = run.get("campaign_id")
            report.check(bool(campaign_id), "a campaign was created")
            if not campaign_id:
                return

            package = (
                await http.get(
                    f"/api/v1/campaign-generation/campaigns/{campaign_id}/package", headers=owner
                )
            ).json()
            assets = [a for c in package["concepts"] for a in c["assets"]]

            # ---- image ---------------------------------------------------
            print("\n   image")
            image_calls = [c for c in recorder.to("api.openai.com") if c.method == "POST"]
            report.check(bool(image_calls), f"the provider API was called ({len(image_calls)} call(s))")
            if image_calls:
                report.check(
                    image_calls[0].status is not None and image_calls[0].status < 400,
                    f"the provider accepted the request (HTTP {image_calls[0].status})",
                )

            images = [a for a in assets if a["kind"] == "image"]
            completed = [a for a in images if a["status"] == "COMPLETED"]
            report.check(bool(completed), f"an image reached COMPLETED ({len(completed)}/{len(images)})")
            for asset in completed[:1]:
                fetched = await http.get(asset["url"], headers=owner)
                report.check(fetched.status_code == 200, "the stored object reads back through the API")
                report.check(
                    fetched.content.startswith(b"\x89PNG") or fetched.content.startswith(b"\xff\xd8\xff"),
                    "the stored object is a real image by magic number",
                )
                report.check(
                    len(fetched.content) > 1000, f"the file has real content ({len(fetched.content)} bytes)"
                )
                report.check(asset["demo"] is False, "the asset is NOT labelled demo")

                row = await find_library_row(http, owner, asset["id"])
                report.check(row is not None, "the creative library lists the asset")
                if row:
                    report.check(
                        row["data_source"] == "live", f"data_source is live (got {row['data_source']})"
                    )
                    report.check(row["is_real"] is True, f"is_real is true (got {row['is_real']})")

                download = await http.get(f"{asset['url']}?download=true", headers=owner)
                report.check(
                    "attachment" in download.headers.get("content-disposition", ""),
                    "authorized download is served as an attachment",
                )
                cross = await http.get(asset["url"], headers=other)
                report.check(cross.status_code == 404, "another organization cannot download it (404, not 403)")

                # Storage must stay behind the API. A bucket URL or a presigned
                # link would outlive the permission that granted it.
                report.check(
                    asset["url"].startswith("/api/") and "://" not in asset["url"],
                    f"the asset is addressed through the authenticated API, not a bucket URL ({asset['url']})",
                )
                anonymous = await http.get(asset["url"])
                report.check(
                    anonymous.status_code in {401, 403},
                    f"the bytes are not readable without a token (got {anonymous.status_code})",
                )

            # ---- video ---------------------------------------------------
            if want_video:
                print("\n   video")
                submits = [
                    c
                    for c in recorder.to("api.replicate.com")
                    if c.method == "POST" and not c.url.endswith("/cancel")
                ]
                polls = [c for c in recorder.to("api.replicate.com") if c.method == "GET"]
                report.check(bool(submits), f"the video provider was called ({len(submits)} submission(s))")
                report.check(
                    bool(polls), f"the job was polled rather than awaited inline ({len(polls)} poll(s))"
                )
                if vendor is not None:
                    report.check(
                        vendor.polls > vendor.polls_before_success,
                        "polling continued through a non-terminal provider status",
                    )

                videos = [a for a in assets if a["kind"] == "video"]
                done = [a for a in videos if a["status"] == "COMPLETED"]
                report.check(bool(done), f"a video reached COMPLETED ({len(done)}/{len(videos)})")
                for asset in done[:1]:
                    fetched = await http.get(asset["url"], headers=owner)
                    report.check(fetched.status_code == 200, "the stored video reads back through the API")
                    report.check(
                        fetched.content[4:8] == b"ftyp" or fetched.content.startswith(b"\x1a\x45\xdf\xa3"),
                        "the stored object is a real video container by signature",
                    )
                    row = await find_library_row(http, owner, asset["id"])
                    if row:
                        report.check(
                            row["data_source"] == "live",
                            f"video data_source is live (got {row['data_source']})",
                        )
                        report.check(row["is_real"] is True, "video is_real is true")
                    cross = await http.get(asset["url"], headers=other)
                    report.check(cross.status_code == 404, "another organization cannot download the video")


async def find_library_row(http: AsyncClient, headers: dict, asset_id: str) -> dict | None:
    rows = (await http.get("/api/v1/creative/assets?limit=200", headers=headers)).json()
    return next((r for r in rows if r["id"] == asset_id), None)


# ---------------------------------------------------------------------------
# Phase 4 — cancellation.
# ---------------------------------------------------------------------------


async def phase_cancellation(report: Report, *, mode: str) -> None:
    print(f"\n4. Video cancellation — the provider must actually be told  [{mode}]")
    with temporary_settings(
        DEMO_MODE="false",
        VIDEO_PROVIDER="replicate",
        VIDEO_API_KEY=STANDIN_KEY,
        VIDEO_MODEL="owner/model",
    ):
        vendor = StandInVendor()
        # Never completes on its own, so there is always a live job to cancel.
        vendor.polls_before_success = 10_000
        recorder = RecordingTransport(httpx.MockTransport(vendor.handler))

        owner_email, client_id = await make_tenant("cancel", demo_mode=False)
        other_email, _ = await make_tenant("cancel-other", demo_mode=False)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=120.0) as http:
            owner = await login(http, owner_email)
            other = await login(http, other_email)

            with vendor_transport(recorder):

                async def submit_job() -> str | None:
                    """Submit and let the worker reach the provider, so a real job id exists."""
                    resp = await http.post(
                        "/api/v1/creative/videos/generate",
                        headers=owner,
                        json={
                            "client_id": str(client_id),
                            "prompt": "A cancellation probe for the video adapter",
                            "aspect_ratio": "9:16",
                            "duration_seconds": 5,
                        },
                    )
                    if resp.status_code >= 400:
                        report.check(False, f"a video job could be created ({resp.status_code}: {resp.text[:200]})")
                        return None
                    # follow_scheduled off: advancing the clock would poll this job
                    # forward, and the point is to cancel one that is still running.
                    await drain_queue(max_cycles=4, follow_scheduled=False)
                    return resp.json().get("job_id")

                job_id = await submit_job()
                if not job_id:
                    return
                report.check(True, "a video job exists to cancel")

                state = (await http.get(f"/api/v1/creative/videos/jobs/{job_id}", headers=owner)).json()
                report.check(
                    state["status"] in {"QUEUED", "SUBMITTED", "GENERATING", "PROCESSING"},
                    f"the job is live before cancelling (status={state['status']})",
                )
                report.check(
                    bool(state.get("provider_job_id")),
                    "the job carries a provider job id, so there is something to cancel remotely",
                )

                # --- cross-tenant cancellation --------------------------------
                foreign = await http.post(
                    f"/api/v1/creative/videos/jobs/{job_id}/cancel", headers=other, json={}
                )
                report.check(
                    foreign.status_code in {404, 400},
                    f"another organization cannot cancel the job (got {foreign.status_code})",
                )
                still = (await http.get(f"/api/v1/creative/videos/jobs/{job_id}", headers=owner)).json()
                report.check(still["status"] != "CANCELLED", "the foreign attempt did not change the job")

                # --- provider accepts the cancellation ------------------------
                before = len(vendor.cancelled)
                cancelled = await http.post(
                    f"/api/v1/creative/videos/jobs/{job_id}/cancel", headers=owner, json={}
                )
                report.check(cancelled.status_code == 200, f"the owner can cancel ({cancelled.status_code})")
                report.check(
                    len(vendor.cancelled) == before + 1,
                    "the provider's cancel endpoint was actually called, not just the database",
                )
                report.check(
                    any(c.url.endswith("/cancel") for c in recorder.calls),
                    "an outbound cancel request was recorded",
                )
                report.check(
                    cancelled.json()["status"] == "CANCELLED",
                    f"the job is CANCELLED locally once the provider agreed (got {cancelled.json()['status']})",
                )

                # --- cancelling a finished job --------------------------------
                repeat = await http.post(
                    f"/api/v1/creative/videos/jobs/{job_id}/cancel", headers=owner, json={}
                )
                body = repeat.json()
                report.check(
                    "already finished" in (body.get("message") or "").lower(),
                    f"a finished job reports there was nothing to cancel ({body.get('message')!r})",
                )
                report.check(
                    len(vendor.cancelled) == before + 1,
                    "no second provider cancellation was issued for an already-finished job",
                )

                # --- provider refuses the cancellation ------------------------
                vendor.cancel_status = 409
                job2_id = await submit_job()
                if job2_id:
                    before_status = (
                        await http.get(f"/api/v1/creative/videos/jobs/{job2_id}", headers=owner)
                    ).json()["status"]
                    refused = await http.post(
                        f"/api/v1/creative/videos/jobs/{job2_id}/cancel", headers=owner, json={}
                    )
                    after = (await http.get(f"/api/v1/creative/videos/jobs/{job2_id}", headers=owner)).json()
                    report.check(
                        any(c.url.endswith("/cancel") and c.status == 409 for c in recorder.calls),
                        "the provider was asked and refused (HTTP 409 recorded)",
                    )
                    report.check(
                        refused.status_code == 502
                        and refused.json()["error"]["code"] == "MEDIA_CANCELLATION_FAILED",
                        f"a refusal returns the structured cancellation error ({refused.status_code})",
                    )
                    report.check(
                        after["status"] != "CANCELLED",
                        f"a refused cancellation is NOT recorded as CANCELLED (status={after['status']})",
                    )
                    report.check(
                        after["status"] == before_status,
                        f"the job keeps its real state ({before_status} -> {after['status']})",
                    )
                    report.check(
                        "cannot cancel" not in refused.text,
                        "the provider's raw refusal body is not echoed to the caller",
                    )


# ---------------------------------------------------------------------------
# Phase 5 — demo and unconfigured classification.
# ---------------------------------------------------------------------------


async def phase_demo_and_unconfigured(report: Report) -> None:
    print("\n5. DEMO and NOT_CONFIGURED classification")
    with preserved_environ(*MEDIA_ENV_KEYS):
        owner_email, client_id = await make_tenant("modes", demo_mode=True)

        reload_settings(IMAGE_PROVIDER="demo", DEMO_MODE="true", IMAGE_API_KEY="")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=120.0) as http:
            owner = await login(http, owner_email)
            created = await http.post(
                "/api/v1/creative/images/generate",
                headers=owner,
                json={"client_id": str(client_id), "prompt": "A demo-mode classification probe", "quantity": 1},
            )
            report.check(created.status_code < 400, f"demo generation is accepted ({created.status_code})")
            await drain_queue(max_cycles=8)
            rows = (await http.get("/api/v1/creative/assets?limit=50", headers=owner)).json()
            demo_rows = [r for r in rows if r["data_source"] == "demo"]
            report.check(bool(demo_rows), "the demo provider produced an asset")
            if demo_rows:
                report.check(demo_rows[0]["is_real"] is False, "a demo asset reports is_real false")
                body = await http.get(f"/api/v1/creative/media/{demo_rows[0]['id']}", headers=owner)
                report.check(
                    body.status_code == 200 and body.content.startswith(b"\x89PNG"),
                    "the demo asset is a real file on disk, honestly labelled",
                )

        reload_settings(IMAGE_PROVIDER="none", VIDEO_PROVIDER="none")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=120.0) as http:
            owner = await login(http, owner_email)
            before = len((await http.get("/api/v1/creative/assets?limit=200", headers=owner)).json())
            resp = await http.post(
                "/api/v1/creative/images/generate",
                headers=owner,
                json={"client_id": str(client_id), "prompt": "An unconfigured-provider probe", "quantity": 1},
            )
            payload = resp.json() if resp.status_code < 500 else {}
            text = (str(payload) + resp.text).upper()
            report.check(
                "NOT_CONFIGURED" in text,
                f"an unconfigured provider reports NOT_CONFIGURED ({resp.status_code})",
            )
            report.check(
                "HTTP" not in str(payload.get("url") or ""),
                "no URL was offered for an asset that does not exist",
            )
            after = len((await http.get("/api/v1/creative/assets?limit=200", headers=owner)).json())
            report.check(after == before, f"no asset row was invented ({before} -> {after})")


# ---------------------------------------------------------------------------


async def main() -> int:
    settings = get_settings()
    report = Report()

    image_key = (settings.image_api_key or settings.openai_api_key or "").strip()
    image_vendor = (settings.image_provider or "none").lower() in {"openai", "dall-e", "dalle"}
    video_key = (settings.video_api_key or "").strip()
    video_model = (settings.video_model or "").strip()
    video_vendor = (settings.video_provider or "none").lower() == "replicate"

    image_mode = "VENDOR" if (image_vendor and image_key) else "STANDIN"
    video_mode = "VENDOR" if (video_vendor and video_key and video_model) else "STANDIN"

    print("P2-A-1 real media provider verification")
    print("=" * 78)
    print(f"  database         {redact(settings.database_url)}")
    print(f"  storage          {settings.storage_backend}")
    print(f"  image provider   {settings.image_provider}  -> {image_mode}")
    print(f"  video provider   {settings.video_provider}  -> {video_mode}")

    if image_mode == "STANDIN":
        report.unconfigured(
            "IMAGE: no real image provider credential is present, so the vendor round trip "
            "was NOT executed. The adapter, validation, storage, asset record and REAL "
            "classification are verified against a local stand-in of the vendor API."
        )
    else:
        report.note("IMAGE: a real credential is present; requests below go to the real vendor.")
    if video_mode == "STANDIN":
        report.unconfigured(
            "VIDEO: no real video provider credential is present, so the vendor round trip "
            "was NOT executed. Submission, polling, download, validation and storage are "
            "verified against a local stand-in of the vendor API."
        )
    else:
        report.note("VIDEO: a real credential is present; requests below go to the real vendor.")

    phase_provider_resolution(report)
    await phase_request_shape(report)

    # One transport for the chain. In VENDOR mode the recorder delegates to the
    # network; in STANDIN mode to the local vendor. The assertions do not change.
    # DEMO_MODE is forced off for the chain: an asset is labelled demo when the
    # provider says so *or* the workspace is in demo mode, so `live` can only be
    # verified in a workspace that is not pretending. That is also the only
    # configuration production runs in.
    #
    # VENDOR mode must keep the process IMAGE_*/VIDEO_* values from invocation
    # (Phase 2 stand-in overrides are restored above). Only DEMO_MODE is forced.
    if image_mode == "VENDOR" or video_mode == "VENDOR":
        vendor = None
        inner: httpx.AsyncBaseTransport = httpx.AsyncHTTPTransport()
        chain_env = {"DEMO_MODE": "false"}
    else:
        vendor = StandInVendor()
        inner = httpx.MockTransport(vendor.handler)
        chain_env = {
            "DEMO_MODE": "false",
            "IMAGE_PROVIDER": "openai",
            "IMAGE_API_KEY": STANDIN_KEY,
            "VIDEO_PROVIDER": "replicate",
            "VIDEO_API_KEY": STANDIN_KEY,
            "VIDEO_MODEL": "owner/model",
        }
    with temporary_settings(**chain_env):
        recorder = RecordingTransport(inner)
        await phase_media_chain(
            report,
            mode=f"image={image_mode} video={video_mode}",
            recorder=recorder,
            vendor=vendor,
            want_video=True,
        )

    await phase_cancellation(report, mode=video_mode)
    await phase_demo_and_unconfigured(report)

    return finish(report)


def redact(url: str) -> str:
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    return f"{scheme}://***@{rest.split('@', 1)[1]}"


def finish(report: Report) -> int:
    total = len(report.checks)
    failed = report.failed
    print("\n" + "=" * 78)
    print(f"{total - len(failed)}/{total} checks passed")
    for text in failed:
        print(f"  FAILED: {text}")
    for text in report.not_configured:
        print(f"\n  NOT_CONFIGURED: {text}")
    for text in report.findings:
        print(f"\n  FINDING: {text}")
    for note in report.notes:
        print(f"\n  {note}")
    if failed:
        return 1
    return 3 if report.not_configured else 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(2)
