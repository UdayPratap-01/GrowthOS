"""P0-10 — server-side role-based authorization for financial and platform actions."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.permissions import Permission, has_permission, permissions_for
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.client import Client
from app.models.enums import MemberRole
from app.models.organization import Organization, OrganizationMember
from app.models.user import User

PASSWORD = "Str0ng-Test-Passw0rd!"

# Actions that spend money, write to an external platform, or change security posture.
SENSITIVE = (
    Permission.campaign_publish,
    Permission.budget_change,
    Permission.financial_action,
    Permission.action_approve,
    Permission.action_execute,
    Permission.integration_connect,
    Permission.integration_disconnect,
    Permission.autonomous_execution,
    Permission.autonomy_manage,
)


# --------------------------------------------------------------------------
# Permission matrix
# --------------------------------------------------------------------------


@pytest.mark.parametrize("permission", SENSITIVE)
def test_member_cannot_perform_sensitive_actions(permission):
    """The exact bug from the audit: invited members could approve financial actions."""
    assert not has_permission(MemberRole.member, permission)


@pytest.mark.parametrize("permission", SENSITIVE)
def test_admin_and_owner_can_perform_sensitive_actions(permission):
    assert has_permission(MemberRole.admin, permission)
    assert has_permission(MemberRole.owner, permission)


def test_viewer_is_read_only():
    assert permissions_for(MemberRole.viewer) == frozenset({Permission.read})
    for permission in Permission:
        if permission is not Permission.read:
            assert not has_permission(MemberRole.viewer, permission)


def test_member_can_still_do_day_to_day_work():
    for permission in (Permission.content_write, Permission.client_write, Permission.lead_write, Permission.read):
        assert has_permission(MemberRole.member, permission)


def test_only_owner_manages_billing():
    assert has_permission(MemberRole.owner, Permission.billing_manage)
    assert not has_permission(MemberRole.admin, Permission.billing_manage)
    assert not has_permission(MemberRole.member, Permission.billing_manage)
    assert not has_permission(MemberRole.viewer, Permission.billing_manage)


def test_roles_are_strictly_nested():
    viewer = permissions_for(MemberRole.viewer)
    member = permissions_for(MemberRole.member)
    admin = permissions_for(MemberRole.admin)
    owner = permissions_for(MemberRole.owner)
    assert viewer < member < admin < owner


def test_unknown_role_gets_least_privilege():
    class Rogue:
        value = "superuser"

    assert permissions_for(Rogue()) == frozenset({Permission.read})  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# End-to-end enforcement
# --------------------------------------------------------------------------


async def _make_user(role: MemberRole) -> tuple[str, uuid.UUID]:
    """Create an org with one client and a single member holding `role`."""
    suffix = uuid.uuid4().hex[:8]
    email = f"{role.value}-{suffix}@rbactest.com"
    async with AsyncSessionLocal() as db:
        user = User(email=email, hashed_password=hash_password(PASSWORD), full_name=f"{role.value} user")
        org = Organization(name=f"RBAC {suffix}", slug=f"rbac-{suffix}", demo_mode=False)
        db.add_all([user, org])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=role))
        client = Client(organization_id=org.id, business_name="RBAC Client", industry="saas")
        db.add(client)
        await db.commit()
        return email, client.id


async def _login(http: AsyncClient, email: str) -> dict[str, str]:
    resp = await http.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [MemberRole.member, MemberRole.viewer])
async def test_lower_roles_are_blocked_from_approving_actions(role):
    email, _ = await _make_user(role)
    action_id = uuid.uuid4()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        headers = await _login(http, email)
        resp = await http.post(f"/api/v1/autopilot/actions/{action_id}/approve", headers=headers)

    # 403 for authorization, never 404 — the permission check must run before lookup.
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [MemberRole.member, MemberRole.viewer])
async def test_lower_roles_are_blocked_from_executing_actions(role):
    email, _ = await _make_user(role)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        headers = await _login(http, email)
        resp = await http.post(f"/api/v1/autopilot/actions/{uuid.uuid4()}/execute", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [MemberRole.member, MemberRole.viewer])
async def test_lower_roles_cannot_connect_or_disconnect_integrations(role):
    email, _ = await _make_user(role)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        headers = await _login(http, email)
        connect = await http.post("/api/v1/integrations/meta/connect", headers=headers, json={})
        disconnect = await http.post("/api/v1/integrations/meta/disconnect", headers=headers, json={})
    assert connect.status_code == 403
    assert disconnect.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [MemberRole.member, MemberRole.viewer])
async def test_lower_roles_cannot_change_autonomy_settings(role):
    email, _ = await _make_user(role)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        headers = await _login(http, email)
        resp = await http.put("/api/v1/autopilot/settings", headers=headers, json={"mode": "autonomous"})
    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [MemberRole.member, MemberRole.viewer])
async def test_lower_roles_cannot_trigger_autonomous_runs(role):
    email, client_id = await _make_user(role)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        headers = await _login(http, email)
        run = await http.post("/api/v1/autopilot/run", headers=headers, json={"client_id": str(client_id)})
        loop = await http.post("/api/v1/autopilot/decision-loop", headers=headers, json={"client_id": str(client_id)})
    assert run.status_code == 403
    assert loop.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [MemberRole.member, MemberRole.viewer])
async def test_lower_roles_cannot_publish_content(role):
    email, client_id = await _make_user(role)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        headers = await _login(http, email)
        resp = await http.post(
            "/api/v1/autopilot/content/publish",
            headers=headers,
            json={"client_id": str(client_id), "platform": "instagram", "content": {}},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_passes_the_permission_gate():
    """An admin must clear authorization; any later error must not be a 403."""
    email, _ = await _make_user(MemberRole.admin)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        headers = await _login(http, email)
        resp = await http.post(f"/api/v1/autopilot/actions/{uuid.uuid4()}/approve", headers=headers)
    assert resp.status_code != 403, "admins must not be blocked by authorization"
    assert resp.status_code == 404, "a nonexistent action should 404 after passing authorization"


@pytest.mark.asyncio
async def test_viewer_cannot_write_but_can_read():
    email, _ = await _make_user(MemberRole.viewer)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        headers = await _login(http, email)
        read = await http.get("/api/v1/clients", headers=headers)
        write = await http.post(
            "/api/v1/clients", headers=headers, json={"business_name": "Nope", "industry": "saas"}
        )
    assert read.status_code == 200, "viewers must retain read access"
    assert write.status_code == 403


@pytest.mark.asyncio
async def test_member_can_write_clients_and_leads():
    email, client_id = await _make_user(MemberRole.member)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        headers = await _login(http, email)
        created = await http.post(
            "/api/v1/clients", headers=headers, json={"business_name": "Allowed", "industry": "saas"}
        )
        lead = await http.post(
            f"/api/v1/clients/{client_id}/leads",
            headers=headers,
            json={"name": "Casey Lin", "email": "casey@example.com"},
        )
    assert created.status_code == 201, created.text
    assert lead.status_code == 201, lead.text


@pytest.mark.asyncio
async def test_unauthenticated_requests_are_rejected_before_permissions():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        resp = await http.post(f"/api/v1/autopilot/actions/{uuid.uuid4()}/approve")
    assert resp.status_code == 401


# --------------------------------------------------------------------------
# Expensive and consequential operations, per role
# --------------------------------------------------------------------------

#: Every endpoint that spends money (AI, media, external API quota) or has an
#: external consequence, with the lowest role that may call it. Checked for all
#: three roles: below the bar must be 403, at or above must clear the gate.
#: What happens after the gate (404, 422, provider not configured) is not this
#: test's business — only that authorization was decided server-side.
EXPENSIVE_ENDPOINTS = [
    # label, method, path, body, minimum role
    ("ai_action_create", "POST", "/api/v1/autopilot/actions", {
        "action_type": "schedule_content", "agent": "SocialMediaAgent",
        "description": "d", "reason": "r",
    }, MemberRole.member),
    ("campaign_propose", "POST", "/api/v1/autopilot/campaigns/propose", {
        "client_id": "{client_id}", "platform": "meta", "name": "C",
        "objective": "leads", "daily_budget": "10.00", "reason": "r",
    }, MemberRole.member),
    ("content_schedule", "POST", "/api/v1/autopilot/content/schedule", {
        "client_id": "{client_id}", "platform": "instagram", "description": "d",
        "scheduled_for": "2030-01-01T00:00:00Z", "content": {},
    }, MemberRole.member),
    ("creative_generate", "POST", "/api/v1/autopilot/creative/generate", {
        "client_id": "{client_id}", "brief": "b",
    }, MemberRole.member),
    ("image_generate", "POST", "/api/v1/autopilot/image/generate", {
        "client_id": "{client_id}", "prompt": "p",
    }, MemberRole.member),
    ("video_generate", "POST", "/api/v1/autopilot/video/generate", {
        "client_id": "{client_id}", "prompt": "p",
    }, MemberRole.member),
    ("creative_variations", "POST", "/api/v1/autopilot/creative/variations", {
        "client_id": "{client_id}", "asset_id": str(uuid.uuid4()),
    }, MemberRole.member),
    ("optimization_analyze", "POST",
     "/api/v1/autopilot/optimization/analyze?client_id={client_id}", None, MemberRole.member),
    ("health_narrative", "GET",
     "/api/v1/autopilot/campaigns/health/summary?client_id={client_id}", None, MemberRole.member),
    ("media_image_job", "POST", "/api/v1/creative/images/generate", {
        "client_id": "{client_id}", "prompt": "p",
    }, MemberRole.member),
    ("media_video_job", "POST", "/api/v1/creative/videos/generate", {
        "client_id": "{client_id}", "prompt": "p",
    }, MemberRole.member),
    ("report_generate", "POST", "/api/v1/clients/{client_id}/reports/generate", {},
     MemberRole.member),
    ("job_retry", "POST", f"/api/v1/jobs/{uuid.uuid4()}/retry", None, MemberRole.member),
    ("job_cancel", "POST", f"/api/v1/jobs/{uuid.uuid4()}/cancel", None, MemberRole.member),
    # Admin and above: money, publishing, credentials, worker execution.
    ("jobs_process", "POST", "/api/v1/autopilot/jobs/process", None, MemberRole.admin),
    ("integration_sync", "POST", "/api/v1/integrations/meta/sync", None, MemberRole.admin),
    ("integration_sync_async", "POST", "/api/v1/integrations/meta/sync/async", None,
     MemberRole.admin),
    ("campaign_build", "POST", "/api/v1/autopilot/campaigns/build", {
        "client_id": "{client_id}", "objective": "leads", "daily_budget": "10.00",
    }, MemberRole.admin),
    ("autopilot_run", "POST", "/api/v1/autopilot/run", {"client_id": "{client_id}"},
     MemberRole.admin),
    ("decision_loop", "POST", "/api/v1/autopilot/decision-loop", {"client_id": "{client_id}"},
     MemberRole.admin),
    ("content_publish", "POST", "/api/v1/autopilot/content/publish", {
        "client_id": "{client_id}", "platform": "instagram", "description": "d", "content": {},
    }, MemberRole.admin),
    # Owner only: commercial terms.
    ("billing_plan", "POST", "/api/v1/billing/plan", {"plan": "growth"}, MemberRole.owner),
]

_ROLE_ORDER = {MemberRole.viewer: 0, MemberRole.member: 1, MemberRole.admin: 2, MemberRole.owner: 3}


def _fill(value, client_id):
    if isinstance(value, str):
        return value.replace("{client_id}", str(client_id))
    if isinstance(value, dict):
        return {key: _fill(item, client_id) for key, item in value.items()}
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [MemberRole.viewer, MemberRole.member, MemberRole.admin])
@pytest.mark.parametrize(
    "label,method,path,body,minimum",
    EXPENSIVE_ENDPOINTS,
    ids=[row[0] for row in EXPENSIVE_ENDPOINTS],
)
async def test_expensive_endpoints_enforce_role_server_side(label, method, path, body, minimum, role):
    email, client_id = await _make_user(role)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        headers = await _login(http, email)
        resp = await http.request(
            method,
            _fill(path, client_id),
            headers=headers,
            json=_fill(body, client_id) if body is not None else None,
        )

    allowed = _ROLE_ORDER[role] >= _ROLE_ORDER[minimum]
    if allowed:
        assert resp.status_code != 403, f"{label}: {role.value} must clear the gate ({resp.text})"
    else:
        assert resp.status_code == 403, f"{label}: {role.value} must be refused ({resp.text})"
        assert resp.json()["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_a_viewer_can_still_read_the_things_it_is_denied_from_running():
    """Read-only must mean read-only, not blind."""
    email, client_id = await _make_user(MemberRole.viewer)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        headers = await _login(http, email)
        reads = {
            "actions": await http.get("/api/v1/autopilot/actions", headers=headers),
            "summary": await http.get("/api/v1/autopilot/summary", headers=headers),
            "assets": await http.get("/api/v1/creative/assets", headers=headers),
            "jobs": await http.get("/api/v1/jobs", headers=headers),
            "integrations": await http.get("/api/v1/integrations", headers=headers),
            "health": await http.get(
                f"/api/v1/autopilot/campaigns/health?client_id={client_id}", headers=headers
            ),
        }
    for name, resp in reads.items():
        assert resp.status_code == 200, f"viewer lost read access to {name}: {resp.text}"


# --------------------------------------------------------------------------
# Coverage guard
# --------------------------------------------------------------------------


def test_every_sensitive_route_is_gated_server_side():
    """
    Fails if a new financial or platform-writing route ships without a
    permission dependency. Frontend button visibility is not a control.
    """
    v1 = Path(__file__).resolve().parents[1] / "app" / "api" / "v1"
    required = {
        ("autopilot.py", "/actions/{action_id}/approve"),
        ("autopilot.py", "/actions/{action_id}/reject"),
        ("autopilot.py", "/actions/{action_id}/execute"),
        ("autopilot.py", "/actions/{action_id}/rollback"),
        ("autopilot.py", "/content/publish"),
        ("autopilot.py", "/campaigns/build"),
        ("autopilot.py", "/decision-loop"),
        ("autopilot.py", "/run"),
        ("autopilot.py", "/settings"),
        ("autopilot.py", "/optimization/rules"),
        # Expensive or consequential operations, added after the production
        # security review found them reachable with plain authentication.
        ("autopilot.py", "/jobs/process"),
        ("autopilot.py", "/actions"),
        ("autopilot.py", "/campaigns/propose"),
        ("autopilot.py", "/content/schedule"),
        ("autopilot.py", "/creative/generate"),
        ("autopilot.py", "/image/generate"),
        ("autopilot.py", "/video/generate"),
        ("autopilot.py", "/creative/variations"),
        ("autopilot.py", "/optimization/analyze"),
        ("integrations.py", "/{provider}/connect"),
        ("integrations.py", "/{provider}/disconnect"),
        ("integrations.py", "/{provider}/sync"),
        ("integrations.py", "/{provider}/sync/async"),
        ("auth.py", "/organization/mode"),
    }
    found = set()
    for path in v1.glob("*.py"):
        for block in re.split(r"(?=^@router\.)", path.read_text(encoding="utf-8"), flags=re.M):
            m = re.match(r'@router\.(get|post|put|patch|delete)\("([^"]*)"', block)
            if not m or m.group(1) == "get":
                continue
            if "require_permission(Permission." in block:
                found.add((path.name, m.group(2)))
    missing = required - found
    assert not missing, f"sensitive routes missing server-side authorization: {sorted(missing)}"


def test_no_write_route_relies_on_bare_authentication_for_money():
    """Money-moving autopilot routes must not use plain get_current_auth."""
    autopilot = (Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "autopilot.py").read_text()
    for block in re.split(r"(?=^@router\.)", autopilot, flags=re.M):
        m = re.match(r'@router\.(post|put|patch|delete)\("([^"]*)"', block)
        if not m:
            continue
        path = m.group(2)
        if any(k in path for k in ("approve", "execute", "publish", "rollback", "/run", "build")):
            assert "require_permission(Permission." in block, f"{path} is not permission-gated"
