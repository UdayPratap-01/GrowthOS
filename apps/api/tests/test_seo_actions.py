"""M9.13 — SEO approval/action system tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.automation import AIAction
from app.models.enums import AIActionStatus, AIActionType, MemberRole
from app.models.organization import OrganizationMember
from app.models.seo import SeoCrawl, SeoCrawlPage
from app.models.seo_internal_link import SeoInternalLinkOpportunity, SeoInternalLinkRun
from app.models.user import User
from app.seo.actions.payload import payload_hash, stamp_approved_payload, verify_approved_payload
from app.seo.actions.types import SEO_ACTION_VERSION
from app.services.seo_action_service import SeoActionService
from app.services.seo_internal_link_service import SeoInternalLinkService
from tests.test_seo_internal_links import _seed_crawl
from tests.test_seo_onpage_optimizer import _auth_client, _seed_content


@pytest.mark.asyncio
async def test_eligibility_rejects_external_urls():
    org_id, _, content = await _seed_content()
    async with AsyncSessionLocal() as db:
        await _seed_crawl(db, org_id)
        await SeoInternalLinkService(db).generate(
            organization_id=org_id, content_id=content.id, use_ai=False
        )
        await db.commit()
        opp = await db.scalar(
            select(SeoInternalLinkOpportunity).where(
                SeoInternalLinkOpportunity.organization_id == org_id
            )
        )
        assert opp is not None
        opp.target_url = "https://evil.com/page"
        await db.commit()
        result = await SeoActionService(db).check_eligibility_internal_link(org_id, opp.id)
        assert not result.eligible
        assert any("TARGET" in r or "NOT_INTERNAL" in r for r in result.reasons)


@pytest.mark.asyncio
async def test_propose_internal_link_action():
    org_id, _, content = await _seed_content()
    async with AsyncSessionLocal() as db:
        await _seed_crawl(db, org_id)
        await SeoInternalLinkService(db).generate(
            organization_id=org_id, content_id=content.id, use_ai=False
        )
        await db.commit()
        opp_id = await db.scalar(
            select(SeoInternalLinkOpportunity.id).where(
                SeoInternalLinkOpportunity.organization_id == org_id
            )
        )
    async with AsyncSessionLocal() as db:
        action = await SeoActionService(db).propose_internal_link(
            organization_id=org_id, opportunity_id=opp_id, user_id=None
        )
        await db.commit()
        assert action.action_type == AIActionType.seo_apply_internal_link
        assert action.status == AIActionStatus.pending
        assert action.requires_approval is True
        assert action.payload.get("capability") == "review_only"
        assert action.payload.get("seo_action_version") == SEO_ACTION_VERSION


@pytest.mark.asyncio
async def test_no_autonomous_execution_on_create():
    org_id, _, content = await _seed_content()
    async with AsyncSessionLocal() as db:
        await _seed_crawl(db, org_id)
        await SeoInternalLinkService(db).generate(
            organization_id=org_id, content_id=content.id, use_ai=False
        )
        await db.commit()
        opp_id = await db.scalar(
            select(SeoInternalLinkOpportunity.id).where(
                SeoInternalLinkOpportunity.organization_id == org_id
            )
        )
        action = await SeoActionService(db).propose_internal_link(
            organization_id=org_id, opportunity_id=opp_id, user_id=None
        )
        await db.commit()
        assert action.status == AIActionStatus.pending
        assert action.executed_at is None


@pytest.mark.asyncio
async def test_idempotency_duplicate_propose():
    org_id, _, content = await _seed_content()
    async with AsyncSessionLocal() as db:
        await _seed_crawl(db, org_id)
        await SeoInternalLinkService(db).generate(
            organization_id=org_id, content_id=content.id, use_ai=False
        )
        await db.commit()
        opp_id = await db.scalar(
            select(SeoInternalLinkOpportunity.id).where(
                SeoInternalLinkOpportunity.organization_id == org_id
            )
        )
        svc = SeoActionService(db)
        first = await svc.propose_internal_link(
            organization_id=org_id, opportunity_id=opp_id, user_id=None
        )
        await db.commit()
        with pytest.raises(HTTPException) as exc:
            await svc.propose_internal_link(
                organization_id=org_id, opportunity_id=opp_id, user_id=None
            )
        assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_payload_immutability_hash():
    payload = {
        "seo_action_version": SEO_ACTION_VERSION,
        "source_type": "internal_link_opportunity",
        "source_id": str(uuid.uuid4()),
        "capability": "review_only",
        "artifact_version": {"updated_at": "2026-01-01T00:00:00+00:00", "version_key": "abc"},
        "target": {"source_url": "https://example.com/a", "target_url": "https://example.com/b"},
        "changes": {"anchor_text": "Test"},
    }
    stamped = stamp_approved_payload(payload)
    ok, _ = verify_approved_payload(stamped)
    assert ok
    tampered = dict(stamped)
    tampered["changes"] = {"anchor_text": "Hacked"}
    ok2, reason = verify_approved_payload(tampered)
    assert not ok2
    assert reason == "PAYLOAD_HASH_MISMATCH"


@pytest.mark.asyncio
async def test_approve_and_execute_review_only():
    org_id, _, content = await _seed_content()
    user_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        await _seed_crawl(db, org_id)
        await SeoInternalLinkService(db).generate(
            organization_id=org_id, content_id=content.id, use_ai=False
        )
        await db.commit()
        opp_id = await db.scalar(
            select(SeoInternalLinkOpportunity.id).where(
                SeoInternalLinkOpportunity.organization_id == org_id
            )
        )
        proposed = await SeoActionService(db).propose_internal_link(
            organization_id=org_id, opportunity_id=opp_id, user_id=user_id
        )
        await db.commit()
        approved = await SeoActionService(db).approve(
            org_id, proposed.id, user_id, note="Looks good"
        )
        await db.commit()
        assert approved.status in {AIActionStatus.completed, AIActionStatus.failed, AIActionStatus.executing}
        row = await db.get(AIAction, proposed.id)
        assert row is not None
        if row.status == AIActionStatus.completed:
            assert row.result.get("review_only") is True
            assert "live website" in (row.result.get("note") or "").lower()
            opp = await db.get(SeoInternalLinkOpportunity, opp_id)
            assert opp.status == "accepted"


@pytest.mark.asyncio
async def test_reject_action():
    org_id, _, content = await _seed_content()
    user_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        await _seed_crawl(db, org_id)
        await SeoInternalLinkService(db).generate(
            organization_id=org_id, content_id=content.id, use_ai=False
        )
        await db.commit()
        opp_id = await db.scalar(
            select(SeoInternalLinkOpportunity.id).where(
                SeoInternalLinkOpportunity.organization_id == org_id
            )
        )
        proposed = await SeoActionService(db).propose_internal_link(
            organization_id=org_id, opportunity_id=opp_id, user_id=user_id
        )
        await db.commit()
        rejected = await SeoActionService(db).reject(org_id, proposed.id, user_id, note="No")
        await db.commit()
        assert rejected.status == AIActionStatus.rejected
        opp = await db.get(SeoInternalLinkOpportunity, opp_id)
        assert opp.status == "rejected"


@pytest.mark.asyncio
async def test_tenant_isolation_api():
    org_a, _, content = await _seed_content()
    org_b, _, _ = await _seed_content()
    async with AsyncSessionLocal() as db:
        await _seed_crawl(db, org_a)
        await SeoInternalLinkService(db).generate(
            organization_id=org_a, content_id=content.id, use_ai=False
        )
        await db.commit()
        opp_id = await db.scalar(
            select(SeoInternalLinkOpportunity.id).where(
                SeoInternalLinkOpportunity.organization_id == org_a
            )
        )
    client = await _auth_client(org_b)
    resp = await client.post(f"/api/v1/seo/actions/propose/internal-link/{opp_id}", json={})
    assert resp.status_code in {403, 404, 400}


@pytest.mark.asyncio
async def test_api_propose_list_summary():
    org_id, _, content = await _seed_content()
    async with AsyncSessionLocal() as db:
        await _seed_crawl(db, org_id)
        await SeoInternalLinkService(db).generate(
            organization_id=org_id, content_id=content.id, use_ai=False
        )
        await db.commit()
        opp_id = await db.scalar(
            select(SeoInternalLinkOpportunity.id).where(
                SeoInternalLinkOpportunity.organization_id == org_id
            )
        )
    client = await _auth_client(org_id)
    prop = await client.post(f"/api/v1/seo/actions/propose/internal-link/{opp_id}", json={})
    assert prop.status_code == 200
    body = prop.json()
    assert body["status"] == "PENDING"
    assert body["payload"]["capability"] == "review_only"
    listing = await client.get("/api/v1/seo/actions")
    assert listing.status_code == 200
    assert listing.json()["total"] >= 1
    summary = await client.get("/api/v1/seo/actions/summary")
    assert summary.status_code == 200
    assert summary.json()["pending"] >= 1


@pytest.mark.asyncio
async def test_stale_payload_blocks_execution():
    org_id, _, content = await _seed_content()
    user_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        await _seed_crawl(db, org_id)
        await SeoInternalLinkService(db).generate(
            organization_id=org_id, content_id=content.id, use_ai=False
        )
        await db.commit()
        opp_id = await db.scalar(
            select(SeoInternalLinkOpportunity.id).where(
                SeoInternalLinkOpportunity.organization_id == org_id
            )
        )
        proposed = await SeoActionService(db).propose_internal_link(
            organization_id=org_id, opportunity_id=opp_id, user_id=user_id
        )
        await db.commit()
        opp = await db.get(SeoInternalLinkOpportunity, opp_id)
        opp.dedupe_key = "changed-after-proposal"
        await db.commit()
        approved = await SeoActionService(db).approve(org_id, proposed.id, user_id, note="ok")
        await db.commit()
        row = await db.get(AIAction, proposed.id)
        assert row.status == AIActionStatus.failed
        err = (row.error or "").upper()
        assert any(token in err for token in ("STALE", "VERSION", "ARTIFACT"))


def test_payload_hash_stable():
    p = {"a": 1, "b": 2}
    assert payload_hash({"seo_action_version": "x", "source_type": "t", "source_id": "1", "capability": "review_only", "artifact_version": {}, "target": p, "changes": p}) == payload_hash(
        {"seo_action_version": "x", "source_type": "t", "source_id": "1", "capability": "review_only", "artifact_version": {}, "target": p, "changes": p}
    )
