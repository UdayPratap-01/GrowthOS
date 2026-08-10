"""
P2-A — tenant isolation and RBAC for the campaign engine.

Two questions, asked of every new endpoint.

**Can organization A reach organization B's data?** Every route is driven with a
real id belonging to another tenant. The required answer is 404, not 403: a
"forbidden" reply confirms the record exists, which is itself a leak.

**Can a role do more than it should?** Generation costs AI and media money, so it
needs `content_write` (member and above). Approval authorises a package for
launch, so it needs `action_approve` (admin and above) — a member must not be
able to sign off their own work, which was the exact class of bug the earlier
security remediation fixed.
"""

from __future__ import annotations

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

os.environ["DEMO_MODE"] = "true"
os.environ["IMAGE_PROVIDER"] = "demo"
os.environ["VIDEO_PROVIDER"] = "none"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["STORAGE_LOCAL_PATH"] = "./storage_test_p2a_security"

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.core.security import hash_password  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.client import Client  # noqa: E402
from app.models.enums import MemberRole  # noqa: E402
from app.models.organization import Organization, OrganizationMember  # noqa: E402
from app.models.user import User  # noqa: E402

PASSWORD = "Str0ng-Test-Passw0rd!"


class Tenant:
    def __init__(self, email: str, organization_id: uuid.UUID, client_id: uuid.UUID) -> None:
        self.email = email
        self.organization_id = organization_id
        self.client_id = client_id
        self.headers: dict[str, str] = {}
        self.run: dict = {}
        self.campaign_id: str = ""
        self.concept_id: str = ""
        self.asset_id: str = ""


async def _tenant(role: MemberRole = MemberRole.owner, label: str = "t") -> Tenant:
    suffix = uuid.uuid4().hex[:8]
    email = f"{label}-{role.value}-{suffix}@p2asec.com"
    async with AsyncSessionLocal() as db:
        user = User(email=email, hashed_password=hash_password(PASSWORD), full_name="Sec tester")
        org = Organization(name=f"Sec {suffix}", slug=f"sec-{suffix}", demo_mode=True)
        db.add_all([user, org])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=role))
        client = Client(
            organization_id=org.id,
            business_name=f"Sec Client {suffix}",
            industry="saas",
            target_audience="Founders",
            products_services="A product",
        )
        db.add(client)
        await db.commit()
        return Tenant(email, org.id, client.id)


async def _login(http: AsyncClient, tenant: Tenant) -> None:
    resp = await http.post("/api/v1/auth/login", json={"email": tenant.email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    tenant.headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _generate(http: AsyncClient, tenant: Tenant) -> None:
    """Give a tenant a complete generated campaign to be probed for."""
    resp = await http.post(
        "/api/v1/campaign-generation/generate",
        headers=tenant.headers,
        json={
            "client_id": str(tenant.client_id),
            "platform": "meta",
            "objective": "lead_generation",
            "concept_quantity": 2,
            "image_quantity": 1,
            "variation_quantity": 1,
        },
    )
    assert resp.status_code == 202, resp.text
    tenant.run = resp.json()
    tenant.campaign_id = tenant.run["campaign_id"]

    package = await http.get(
        f"/api/v1/campaign-generation/campaigns/{tenant.campaign_id}/package",
        headers=tenant.headers,
    )
    assert package.status_code == 200, package.text
    body = package.json()
    tenant.concept_id = body["concepts"][0]["id"]
    stored = [a for c in body["concepts"] for a in c["assets"] if a["status"] == "COMPLETED"]
    assert stored, "the fixture needs one stored asset to probe for"
    tenant.asset_id = stored[0]["id"]


# --------------------------------------------------------------------------
# Cross-tenant reads and writes
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_organization_a_cannot_reach_organization_b():
    victim = await _tenant(label="victim")
    attacker = await _tenant(label="attacker")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        await _login(http, victim)
        await _login(http, attacker)
        await _generate(http, victim)

        probes = {
            "run": await http.get(
                f"/api/v1/campaign-generation/runs/{victim.run['id']}", headers=attacker.headers
            ),
            "package": await http.get(
                f"/api/v1/campaign-generation/campaigns/{victim.campaign_id}/package",
                headers=attacker.headers,
            ),
            "approve": await http.post(
                f"/api/v1/campaign-generation/campaigns/{victim.campaign_id}/approve",
                headers=attacker.headers,
                json={},
            ),
            "reject": await http.post(
                f"/api/v1/campaign-generation/campaigns/{victim.campaign_id}/reject",
                headers=attacker.headers,
                json={"reason": "not mine"},
            ),
            "variations": await http.post(
                f"/api/v1/campaign-generation/concepts/{victim.concept_id}/variations",
                headers=attacker.headers,
                json={"count": 1},
            ),
            "regenerate": await http.post(
                f"/api/v1/campaign-generation/concepts/{victim.concept_id}/regenerate",
                headers=attacker.headers,
                json={"image_quantity": 1},
            ),
            "archive_concept": await http.post(
                f"/api/v1/campaign-generation/concepts/{victim.concept_id}/archive",
                headers=attacker.headers,
            ),
            "download": await http.get(
                f"/api/v1/creative/media/{victim.asset_id}?download=true", headers=attacker.headers
            ),
            "archive_asset": await http.post(
                f"/api/v1/creative/assets/{victim.asset_id}/archive", headers=attacker.headers
            ),
            "generate_for_their_client": await http.post(
                "/api/v1/campaign-generation/generate",
                headers=attacker.headers,
                json={"client_id": str(victim.client_id), "platform": "meta"},
            ),
        }

        # The victim's own campaign is untouched by all of that.
        after = await http.get(
            f"/api/v1/campaign-generation/campaigns/{victim.campaign_id}/package",
            headers=victim.headers,
        )

    for name, resp in probes.items():
        assert resp.status_code == 404, f"{name} leaked across tenants: {resp.status_code} {resp.text}"

    body = after.json()
    assert body["campaign"]["review_status"] == "READY_FOR_REVIEW", "state must be unchanged"
    assert body["approval"]["approved_by"] is None
    assert body["approval"]["rejected_by"] is None
    assert len(body["concepts"][0]["variations"]) == 1, "no variation may have been added"


@pytest.mark.asyncio
async def test_listings_never_include_another_tenants_records():
    victim = await _tenant(label="victim")
    attacker = await _tenant(label="attacker")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        await _login(http, victim)
        await _login(http, attacker)
        await _generate(http, victim)

        runs = await http.get("/api/v1/campaign-generation/runs", headers=attacker.headers)
        campaigns = await http.get("/api/v1/campaign-generation/campaigns", headers=attacker.headers)
        assets = await http.get("/api/v1/creative/assets", headers=attacker.headers)
        # Filtering by the victim's ids must not act as a lookup either.
        filtered_runs = await http.get(
            f"/api/v1/campaign-generation/runs?client_id={victim.client_id}",
            headers=attacker.headers,
        )
        filtered_assets = await http.get(
            f"/api/v1/creative/assets?campaign_id={victim.campaign_id}", headers=attacker.headers
        )

    assert runs.json() == []
    assert campaigns.json() == []
    assert assets.json() == []
    assert filtered_runs.json() == []
    assert filtered_assets.json() == []


@pytest.mark.asyncio
async def test_a_media_job_cannot_be_cancelled_across_tenants():
    victim = await _tenant(label="victim")
    attacker = await _tenant(label="attacker")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        await _login(http, victim)
        await _login(http, attacker)
        created = await http.post(
            "/api/v1/creative/images/generate",
            headers=victim.headers,
            json={"client_id": str(victim.client_id), "prompt": "a storefront"},
        )
        job_id = created.json()["job_id"]

        cancelled = await http.post(
            f"/api/v1/creative/images/jobs/{job_id}/cancel", headers=attacker.headers
        )
        read = await http.get(f"/api/v1/creative/images/jobs/{job_id}", headers=attacker.headers)

    assert cancelled.status_code == 404
    assert read.status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_callers_are_refused_everywhere():
    tenant = await _tenant()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        await _login(http, tenant)
        await _generate(http, tenant)

        anonymous = {
            "options": await http.get("/api/v1/campaign-generation/options"),
            "generate": await http.post(
                "/api/v1/campaign-generation/generate", json={"client_id": str(tenant.client_id)}
            ),
            "run": await http.get(f"/api/v1/campaign-generation/runs/{tenant.run['id']}"),
            "package": await http.get(
                f"/api/v1/campaign-generation/campaigns/{tenant.campaign_id}/package"
            ),
            "approve": await http.post(
                f"/api/v1/campaign-generation/campaigns/{tenant.campaign_id}/approve", json={}
            ),
            "media": await http.get(f"/api/v1/creative/media/{tenant.asset_id}"),
        }

    for name, resp in anonymous.items():
        assert resp.status_code in {401, 403}, f"{name} was reachable unauthenticated"


# --------------------------------------------------------------------------
# RBAC
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [MemberRole.viewer, MemberRole.member])
async def test_only_admins_and_above_may_approve_or_reject(role):
    """A member must not be able to sign off the campaign they generated."""
    owner = await _tenant(label="owner")
    subordinate = await _tenant(role=role, label="sub")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        await _login(http, owner)
        await _generate(http, owner)
        await _login(http, subordinate)

        # Against their own organization's campaign id shape; authorization must
        # be decided before any lookup, so this is a 403 either way.
        approve = await http.post(
            f"/api/v1/campaign-generation/campaigns/{owner.campaign_id}/approve",
            headers=subordinate.headers,
            json={},
        )
        reject = await http.post(
            f"/api/v1/campaign-generation/campaigns/{owner.campaign_id}/reject",
            headers=subordinate.headers,
            json={"reason": "no"},
        )

    assert approve.status_code == 403, approve.text
    assert approve.json()["error"]["code"] == "PERMISSION_DENIED"
    assert reject.status_code == 403


@pytest.mark.asyncio
async def test_a_viewer_cannot_spend_money_but_can_read():
    viewer = await _tenant(role=MemberRole.viewer, label="viewer")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        await _login(http, viewer)
        writes = {
            "generate": await http.post(
                "/api/v1/campaign-generation/generate",
                headers=viewer.headers,
                json={"client_id": str(viewer.client_id), "platform": "meta"},
            ),
            "variations": await http.post(
                f"/api/v1/campaign-generation/concepts/{uuid.uuid4()}/variations",
                headers=viewer.headers,
                json={"count": 1},
            ),
            "regenerate": await http.post(
                f"/api/v1/campaign-generation/concepts/{uuid.uuid4()}/regenerate",
                headers=viewer.headers,
                json={"image_quantity": 1},
            ),
            "archive_concept": await http.post(
                f"/api/v1/campaign-generation/concepts/{uuid.uuid4()}/archive",
                headers=viewer.headers,
            ),
            "archive_asset": await http.post(
                f"/api/v1/creative/assets/{uuid.uuid4()}/archive", headers=viewer.headers
            ),
            "cancel": await http.post(
                f"/api/v1/creative/videos/jobs/{uuid.uuid4()}/cancel", headers=viewer.headers
            ),
        }
        reads = {
            "options": await http.get(
                "/api/v1/campaign-generation/options", headers=viewer.headers
            ),
            "runs": await http.get("/api/v1/campaign-generation/runs", headers=viewer.headers),
            "campaigns": await http.get(
                "/api/v1/campaign-generation/campaigns", headers=viewer.headers
            ),
        }

    for name, resp in writes.items():
        assert resp.status_code == 403, f"a viewer reached {name}: {resp.status_code}"
    for name, resp in reads.items():
        assert resp.status_code == 200, f"a viewer lost read access to {name}: {resp.text}"


@pytest.mark.asyncio
async def test_a_member_may_generate_and_vary_but_not_approve():
    member = await _tenant(role=MemberRole.member, label="member")
    admin = await _tenant(role=MemberRole.admin, label="admin")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        await _login(http, member)
        await _generate(http, member)

        variations = await http.post(
            f"/api/v1/campaign-generation/concepts/{member.concept_id}/variations",
            headers=member.headers,
            json={"count": 1},
        )
        self_approve = await http.post(
            f"/api/v1/campaign-generation/campaigns/{member.campaign_id}/approve",
            headers=member.headers,
            json={},
        )

        # An admin in a different organization still cannot approve it — role and
        # tenancy are independent checks.
        await _login(http, admin)
        other_admin = await http.post(
            f"/api/v1/campaign-generation/campaigns/{member.campaign_id}/approve",
            headers=admin.headers,
            json={},
        )

    assert variations.status_code == 200, variations.text
    assert self_approve.status_code == 403
    assert other_admin.status_code == 404


@pytest.mark.asyncio
async def test_an_admin_in_the_owning_organization_can_approve():
    """The positive case, so the checks above cannot pass by blocking everyone."""
    suffix = uuid.uuid4().hex[:8]
    member_email = f"gen-{suffix}@p2asec.com"
    admin_email = f"appr-{suffix}@p2asec.com"
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"Shared {suffix}", slug=f"shared-{suffix}", demo_mode=True)
        generator = User(
            email=member_email, hashed_password=hash_password(PASSWORD), full_name="Generator"
        )
        approver = User(
            email=admin_email, hashed_password=hash_password(PASSWORD), full_name="Approver"
        )
        db.add_all([org, generator, approver])
        await db.flush()
        db.add_all(
            [
                OrganizationMember(
                    organization_id=org.id, user_id=generator.id, role=MemberRole.member
                ),
                OrganizationMember(
                    organization_id=org.id, user_id=approver.id, role=MemberRole.admin
                ),
            ]
        )
        client = Client(organization_id=org.id, business_name="Shared Co", industry="saas")
        db.add(client)
        await db.commit()
        client_id = client.id

    generator_tenant = Tenant(member_email, org.id, client_id)
    approver_tenant = Tenant(admin_email, org.id, client_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as http:
        await _login(http, generator_tenant)
        await _generate(http, generator_tenant)
        await _login(http, approver_tenant)
        approved = await http.post(
            f"/api/v1/campaign-generation/campaigns/{generator_tenant.campaign_id}/approve",
            headers=approver_tenant.headers,
            json={"comment": "Approved after review."},
        )

    assert approved.status_code == 200, approved.text
    approval = approved.json()["approval"]
    assert approval["review_status"] == "READY_TO_PUBLISH"
    # Separation of duties is recorded: the approver is not the generator.
    assert approval["approved_by"] is not None
