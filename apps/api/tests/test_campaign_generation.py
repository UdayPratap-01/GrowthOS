"""
P2-A — AI creative & campaign engine.

These tests drive the real HTTP routes, the real service, the real job handlers
and the real storage backend. The image provider is the `demo` provider, which
produces a genuine PNG and stores it: that exercises the whole
provider → bytes → storage → asset → authenticated-read path without needing a
paid vendor key. What it does *not* prove is that a commercial provider works;
that requires the real-provider verification run, and nothing here should be read
as evidence of it.

The video provider is left unconfigured on purpose, so the NOT_CONFIGURED path is
covered by the same run.
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
os.environ["STORAGE_LOCAL_PATH"] = "./storage_test_campaign"

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.core.security import hash_password  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.generation.media_utils import is_valid_image  # noqa: E402
from app.main import app  # noqa: E402
from app.models.client import Client  # noqa: E402
from app.models.creative import CreativeConcept, CreativeVariation  # noqa: E402
from app.models.enums import MemberRole  # noqa: E402
from app.models.organization import Organization, OrganizationMember  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.usage_service import Metric, UsageService  # noqa: E402

PASSWORD = "Str0ng-Test-Passw0rd!"


async def _make_org(role: MemberRole = MemberRole.owner) -> tuple[str, uuid.UUID, uuid.UUID]:
    """A fresh organization with one client and one member holding `role`."""
    suffix = uuid.uuid4().hex[:8]
    email = f"{role.value}-{suffix}@p2atest.com"
    async with AsyncSessionLocal() as db:
        user = User(email=email, hashed_password=hash_password(PASSWORD), full_name="P2A tester")
        org = Organization(name=f"P2A {suffix}", slug=f"p2a-{suffix}", demo_mode=False)
        db.add_all([user, org])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=role))
        client = Client(
            organization_id=org.id,
            business_name="Northstar Dental",
            industry="dentistry",
            location="Leeds",
            description="Private dental practice offering implants and whitening.",
            target_audience="Adults 30-55 within 10 miles of Leeds",
            products_services="Dental implants, teeth whitening, hygiene plans",
            marketing_goals="More implant consultations",
            brand_voice="Calm, precise, reassuring",
            primary_channels=["meta"],
            kpis=["Cost per consultation"],
        )
        db.add(client)
        await db.commit()
        return email, org.id, client.id


async def _login(http: AsyncClient, email: str) -> dict[str, str]:
    resp = await http.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _stage(run: dict, key: str) -> dict:
    for stage in run["stages"]:
        if stage["key"] == key:
            return stage
    raise AssertionError(f"stage {key} missing from {[s['key'] for s in run['stages']]}")


async def _generate(http: AsyncClient, headers: dict, client_id: uuid.UUID, **overrides) -> dict:
    payload = {
        "client_id": str(client_id),
        "platform": "meta",
        "objective": "lead_generation",
        "offer": "Free implant consultation this month",
        "audience": "Adults 30-55 in Leeds considering implants",
        "tone": "Calm and precise",
        "cta": "Book now",
        "total_budget": "3000.00",
        "daily_budget": "100.00",
        "concept_quantity": 3,
        "image_quantity": 2,
        "video_quantity": 0,
        "variation_quantity": 2,
        "aspect_ratios": ["1:1", "4:5"],
        **overrides,
    }
    resp = await http.post("/api/v1/campaign-generation/generate", headers=headers, json=payload)
    assert resp.status_code == 202, resp.text
    return resp.json()


# --------------------------------------------------------------------------
# Generator options
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_options_expose_configuration_without_claiming_publishing():
    email, _org_id, _client_id = await _make_org()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        headers = await _login(http, email)
        resp = await http.get("/api/v1/campaign-generation/options", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    keys = {p["key"] for p in body["platforms"]}
    assert {"meta", "instagram", "google", "linkedin"} <= keys
    # P2-A publishes nothing, so no platform may advertise that it can.
    assert all(p["publishing_supported"] is False for p in body["platforms"])
    # A fresh organization has connected nothing.
    assert all(p["connected"] is False for p in body["platforms"])
    assert {o["key"] for o in body["objectives"]} >= {
        "lead_generation",
        "sales",
        "traffic",
        "engagement",
        "awareness",
        "conversions",
    }
    assert {r["key"] for r in body["aspect_ratios"]} == {"1:1", "4:5", "9:16", "16:9"}
    assert body["limits"]["max_images"] >= 1
    assert body["media"]["image_configured"] is True
    assert body["media"]["video_configured"] is False


@pytest.mark.asyncio
async def test_unknown_platform_and_objective_are_refused():
    email, _org_id, client_id = await _make_org()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        headers = await _login(http, email)
        bad_platform = await http.post(
            "/api/v1/campaign-generation/generate",
            headers=headers,
            json={"client_id": str(client_id), "platform": "myspace"},
        )
        bad_objective = await http.post(
            "/api/v1/campaign-generation/generate",
            headers=headers,
            json={"client_id": str(client_id), "objective": "world_domination"},
        )

    assert bad_platform.status_code == 400
    assert bad_platform.json()["error"]["code"] == "INVALID_CAMPAIGN_REQUEST"
    assert bad_objective.status_code == 400
    assert bad_objective.json()["error"]["code"] == "INVALID_CAMPAIGN_REQUEST"


# --------------------------------------------------------------------------
# End-to-end generation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generation_produces_a_complete_reviewable_package():
    email, _org_id, client_id = await _make_org()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        headers = await _login(http, email)
        run = await _generate(http, headers, client_id)

        # Stages exist from the first response so the UI has a checklist to draw.
        assert [s["key"] for s in run["stages"]] == [
            "context",
            "strategy",
            "brief",
            "copy",
            "concepts",
            "images",
            "videos",
            "variations",
            "structure",
        ]

        polled = await http.get(f"/api/v1/campaign-generation/runs/{run['id']}", headers=headers)
        assert polled.status_code == 200, polled.text
        run = polled.json()
        assert run["status"] == "READY_FOR_REVIEW", run
        assert run["campaign_id"]

        # Real image generation through the demo provider: two jobs, both stored.
        images = _stage(run, "images")
        assert images["status"] == "COMPLETED", images
        assert images["completed"] == 2 and images["total"] == 2
        # No video provider configured — reported, not failed, and never faked.
        assert _stage(run, "videos")["status"] == "SKIPPED"

        package = await http.get(
            f"/api/v1/campaign-generation/campaigns/{run['campaign_id']}/package", headers=headers
        )
        assert package.status_code == 200, package.text
        body = package.json()

        assert body["publishing_note"].startswith("Publishing is not implemented")
        assert body["campaign"]["review_status"] == "READY_FOR_REVIEW"
        # Platform delivery state must stay draft: nothing was sent anywhere.
        assert body["campaign"]["status"] == "draft"
        assert body["approval"]["external_id"] is None

        strategy = body["strategy"]
        for section in (
            "current_situation",
            "problem",
            "opportunity",
            "target_audience",
            "positioning",
            "core_message",
            "offer_strategy",
            "creative_strategy",
            "channel_strategy",
            "campaign_objective",
        ):
            assert strategy[section], f"strategy section {section} is empty"
        assert strategy["success_metrics"]
        # A client with no analytics must produce stated limitations, not numbers.
        assert strategy["data_limitations"]
        assert body["data_limitations"]

        brief = body["brief"]
        assert brief["campaign_name"]
        assert brief["objective"] == "lead_generation"
        # Budget comes from the request, never from the model.
        assert brief["daily_budget"] == "100.00"

        concepts = body["concepts"]
        assert len(concepts) == 3
        # Three concepts must be three different bets, not three wordings.
        assert len({c["angle"] for c in concepts}) == 3
        assert len({c["hook"] for c in concepts}) == 3
        assert all(c["primary_text"] and c["headline"] and c["cta"] for c in concepts)
        assert all(c["image_prompt"] for c in concepts)
        # Baseline negative constraints are applied in code, not left to the model.
        assert all("no garbled or misspelled text" in c["negative_constraints"] for c in concepts)

        stored = [a for c in concepts for a in c["assets"] if a["status"] == "COMPLETED"]
        assert len(stored) == 2, [a["status"] for c in concepts for a in c["assets"]]
        for asset in stored:
            assert asset["url"], "a COMPLETED asset must be retrievable"
            fetched = await http.get(asset["url"], headers=headers)
            assert fetched.status_code == 200
            assert is_valid_image(fetched.content), "stored object must be a valid image"

        variations = [v for c in concepts for v in c["variations"]]
        assert len(variations) == 2
        assert all(v["hypothesis"] for v in variations)
        assert len({v["axis"] for v in variations}) == 2, "each variation changes a different axis"

        assert body["ad_sets"], "a reviewable package needs at least one ad set"
        assert body["ads"]
        assert all(ad["headline"] and ad["primary_text"] for ad in body["ads"])
        assert all(ad["status"] == "draft" for ad in body["ads"])

        # Daily budget is split across ad sets and never exceeds what was asked for.
        allocated = sum(float(a["daily_budget"]) for a in body["ad_sets"] if a["daily_budget"])
        assert allocated <= 100.01


@pytest.mark.asyncio
async def test_generation_meters_usage_against_the_organization():
    email, org_id, client_id = await _make_org()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        headers = await _login(http, email)
        await _generate(http, headers, client_id, image_quantity=1, variation_quantity=1)

    async with AsyncSessionLocal() as db:
        usage = UsageService(db)
        assert await usage.total(org_id, Metric.CAMPAIGN_GENERATION) == 1
        assert await usage.total(org_id, Metric.STRATEGY_GENERATION) == 1
        assert await usage.total(org_id, Metric.COPY_GENERATION) == 3
        assert await usage.total(org_id, Metric.VARIATION_GENERATION) == 1
        assert await usage.total(org_id, Metric.IMAGE_GENERATION) == 1


@pytest.mark.asyncio
async def test_repeating_an_idempotency_key_does_not_start_a_second_run():
    email, org_id, client_id = await _make_org()
    key = f"double-click-{uuid.uuid4().hex[:8]}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        headers = await _login(http, email)
        first = await _generate(
            http, headers, client_id, image_quantity=1, variation_quantity=0, idempotency_key=key
        )
        second = await _generate(
            http, headers, client_id, image_quantity=1, variation_quantity=0, idempotency_key=key
        )

    assert first["id"] == second["id"]
    async with AsyncSessionLocal() as db:
        # And the retry did not bill a second campaign.
        assert await UsageService(db).total(org_id, Metric.CAMPAIGN_GENERATION) == 1


@pytest.mark.asyncio
async def test_quantities_are_clamped_server_side():
    """The frontend's limits are a courtesy; the backend's are the control."""
    email, _org_id, client_id = await _make_org()
    settings = get_settings()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        headers = await _login(http, email)
        run = await _generate(
            http,
            headers,
            client_id,
            concept_quantity=20,
            image_quantity=50,
            video_quantity=20,
            variation_quantity=50,
        )

    assert run["concept_quantity"] == settings.max_concepts_per_generation
    assert run["image_quantity"] == settings.max_images_per_generation
    assert run["video_quantity"] == settings.max_videos_per_generation
    assert run["variation_quantity"] == settings.max_variations_per_generation


@pytest.mark.asyncio
async def test_video_request_reports_not_configured_and_never_fabricates_a_file():
    email, _org_id, client_id = await _make_org()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        headers = await _login(http, email)
        run = await _generate(http, headers, client_id, image_quantity=0, video_quantity=2)
        run = (
            await http.get(f"/api/v1/campaign-generation/runs/{run['id']}", headers=headers)
        ).json()

        videos = _stage(run, "videos")
        assert videos["status"] == "NOT_CONFIGURED", videos
        assert "no video" in (videos["detail"] or "").lower()
        # The rest of the campaign still generated.
        assert run["status"] == "READY_FOR_REVIEW"

        package = (
            await http.get(
                f"/api/v1/campaign-generation/campaigns/{run['campaign_id']}/package",
                headers=headers,
            )
        ).json()
        assets = [a for c in package["concepts"] for a in c["assets"]]
        assert not [a for a in assets if a["kind"] == "video"], "no video job may exist"
        # The prompts that would have been used are still shown to the reviewer.
        assert all(c["video_prompt"] for c in package["concepts"])
        assert package["media"]["video_configured"] is False


# --------------------------------------------------------------------------
# Variations
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_variations_are_single_axis_and_linked_to_their_parent():
    email, org_id, client_id = await _make_org()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        headers = await _login(http, email)
        run = await _generate(http, headers, client_id, image_quantity=0, variation_quantity=0)
        package = (
            await http.get(
                f"/api/v1/campaign-generation/campaigns/{run['campaign_id']}/package",
                headers=headers,
            )
        ).json()
        concept = package["concepts"][0]

        resp = await http.post(
            f"/api/v1/campaign-generation/concepts/{concept['id']}/variations",
            headers=headers,
            json={"count": 3, "generate_media": False},
        )
        assert resp.status_code == 200, resp.text
        variations = resp.json()

    assert len(variations) == 3
    assert all(v["parent_concept_id"] == concept["id"] for v in variations)
    assert all(v["hypothesis"] for v in variations)
    assert len({v["axis"] for v in variations}) == 3
    # References must not collide with the concept letters already in use.
    used = {c["reference"] for c in package["concepts"]}
    assert not used & {v["reference"] for v in variations}

    async with AsyncSessionLocal() as db:
        rows = await db.scalars(
            select(CreativeVariation).where(CreativeVariation.organization_id == org_id)
        )
        assert len(list(rows)) == 3
        assert await UsageService(db).total(org_id, Metric.VARIATION_GENERATION) == 3


@pytest.mark.asyncio
async def test_regenerating_a_concept_renders_the_stored_prompt_again():
    email, _org_id, client_id = await _make_org()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        headers = await _login(http, email)
        run = await _generate(http, headers, client_id, image_quantity=1, variation_quantity=0)
        package = (
            await http.get(
                f"/api/v1/campaign-generation/campaigns/{run['campaign_id']}/package",
                headers=headers,
            )
        ).json()
        concept = next(c for c in package["concepts"] if c["assets"])

        resp = await http.post(
            f"/api/v1/campaign-generation/concepts/{concept['id']}/regenerate",
            headers=headers,
            json={"image_quantity": 1, "video_quantity": 0},
        )
        assert resp.status_code == 200, resp.text
        assets = resp.json()

    completed = [a for a in assets if a["status"] == "COMPLETED"]
    assert len(completed) == 2, "the original render plus the new one"
    assert all(a["url"] for a in completed)


# --------------------------------------------------------------------------
# Approval
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_records_who_and_when_and_moves_to_ready_to_publish():
    email, _org_id, client_id = await _make_org()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        headers = await _login(http, email)
        run = await _generate(http, headers, client_id, image_quantity=0, variation_quantity=0)
        campaign_id = run["campaign_id"]

        approved = await http.post(
            f"/api/v1/campaign-generation/campaigns/{campaign_id}/approve",
            headers=headers,
            json={"comment": "Angle B is the strongest. Ship it."},
        )
        assert approved.status_code == 200, approved.text
        approval = approved.json()["approval"]
        assert approval["review_status"] == "READY_TO_PUBLISH"
        assert approval["approved_by"] and approval["approved_at"]
        assert approval["approval_comment"] == "Angle B is the strongest. Ship it."
        # Approval authorises nothing external: still no campaign id anywhere.
        assert approval["external_id"] is None
        assert approved.json()["campaign"]["status"] == "draft"

        # Approving twice is a conflict, not a silent no-op.
        again = await http.post(
            f"/api/v1/campaign-generation/campaigns/{campaign_id}/approve", headers=headers, json={}
        )
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "INVALID_CAMPAIGN_STATE"


@pytest.mark.asyncio
async def test_rejection_records_a_reason():
    email, _org_id, client_id = await _make_org()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        headers = await _login(http, email)
        run = await _generate(http, headers, client_id, image_quantity=0, variation_quantity=0)

        rejected = await http.post(
            f"/api/v1/campaign-generation/campaigns/{run['campaign_id']}/reject",
            headers=headers,
            json={"reason": "The offer is not one we can honour in July."},
        )
        assert rejected.status_code == 200, rejected.text
        approval = rejected.json()["approval"]
        assert approval["review_status"] == "REJECTED"
        assert approval["rejected_by"] and approval["rejected_at"]
        assert approval["rejection_reason"].startswith("The offer is not one")

        # A rejected campaign can be approved after revision, and that clears
        # the rejection rather than leaving both recorded.
        approved = await http.post(
            f"/api/v1/campaign-generation/campaigns/{run['campaign_id']}/approve",
            headers=headers,
            json={},
        )
        assert approved.status_code == 200
        assert approved.json()["approval"]["rejection_reason"] is None


@pytest.mark.asyncio
async def test_rejection_requires_a_reason():
    email, _org_id, client_id = await _make_org()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        headers = await _login(http, email)
        run = await _generate(http, headers, client_id, image_quantity=0, variation_quantity=0)
        resp = await http.post(
            f"/api/v1/campaign-generation/campaigns/{run['campaign_id']}/reject",
            headers=headers,
            json={"reason": ""},
        )
    assert resp.status_code == 422


# --------------------------------------------------------------------------
# Runs listing
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runs_and_campaigns_are_listed_for_the_owning_organization():
    email, _org_id, client_id = await _make_org()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        headers = await _login(http, email)
        await _generate(http, headers, client_id, image_quantity=0, variation_quantity=0)

        runs = await http.get("/api/v1/campaign-generation/runs", headers=headers)
        campaigns = await http.get("/api/v1/campaign-generation/campaigns", headers=headers)

    assert runs.status_code == 200
    assert len(runs.json()) == 1
    assert runs.json()[0]["poll_url"].endswith(runs.json()[0]["id"])
    assert campaigns.status_code == 200
    assert len(campaigns.json()) == 1
    assert campaigns.json()[0]["generated_by_ai"] is True
    assert campaigns.json()[0]["review_status"] == "READY_FOR_REVIEW"


@pytest.mark.asyncio
async def test_concept_can_be_archived_and_restored():
    email, org_id, client_id = await _make_org()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        headers = await _login(http, email)
        run = await _generate(http, headers, client_id, image_quantity=0, variation_quantity=0)
        package = (
            await http.get(
                f"/api/v1/campaign-generation/campaigns/{run['campaign_id']}/package",
                headers=headers,
            )
        ).json()
        concept_id = package["concepts"][0]["id"]

        archived = await http.post(
            f"/api/v1/campaign-generation/concepts/{concept_id}/archive?archived=true",
            headers=headers,
        )
        assert archived.status_code == 200, archived.text
        assert archived.json()["status"] == "ARCHIVED"
        assert archived.json()["archived_at"]

        restored = await http.post(
            f"/api/v1/campaign-generation/concepts/{concept_id}/archive?archived=false",
            headers=headers,
        )
        assert restored.json()["status"] == "READY"
        assert restored.json()["archived_at"] is None

    async with AsyncSessionLocal() as db:
        concept = await db.get(CreativeConcept, uuid.UUID(concept_id))
        assert concept is not None and concept.organization_id == org_id
