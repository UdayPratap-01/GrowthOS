"""Stale EXECUTING AI action recovery."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.automation.execution import ExecutionEngine
from app.automation.idempotency import try_claim_action_for_execution
from app.automation.stale_recovery import (
    STALE_RECOVERY_ERROR_PREFIX,
    recover_stale_action,
    reap_stale_executing_actions,
    validate_stale_recovery_settings,
)
from app.core.config import Settings, get_settings
from app.core.security import hash_password
from app.core.startup_checks import ConfigurationError, validate_configuration
from app.db.session import AsyncSessionLocal
from app.models.ai_ops import AuditLog
from app.models.automation import AIAction, ActionExecution
from app.models.client import Client
from app.models.enums import AIActionStatus, AIActionType, MemberRole
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.worker import Worker


async def _seed_action(*, demo_mode: bool = True, status=AIActionStatus.executing):
    async with AsyncSessionLocal() as db:
        org = Organization(
            name=f"Stale Org {uuid.uuid4().hex[:6]}",
            slug=f"stale-{uuid.uuid4().hex[:8]}",
            demo_mode=demo_mode,
        )
        user = User(
            email=f"stale-{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=hash_password("pass"),
            full_name="Stale Tester",
        )
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        client = Client(organization_id=org.id, business_name="Stale Client", industry="saas")
        db.add(client)
        await db.flush()
        stale_at = (
            datetime.now(timezone.utc) - timedelta(minutes=45)
            if status == AIActionStatus.executing
            else None
        )
        action = AIAction(
            organization_id=org.id,
            client_id=client.id,
            action_type=AIActionType.create_creative,
            description="stale test",
            reason="stale test",
            status=status,
            requires_approval=False,
            demo_mode=demo_mode,
            idempotency_key=f"stale-{uuid.uuid4().hex[:12]}",
            executing_at=stale_at,
        )
        db.add(action)
        await db.flush()
        if status == AIActionStatus.executing:
            db.add(
                ActionExecution(
                    organization_id=org.id,
                    action_id=action.id,
                    status=AIActionStatus.executing,
                    started_at=action.executing_at,
                )
            )
        await db.commit()
        return org.id, client.id, action.id, action.idempotency_key


def test_validate_stale_timeout_minimum():
    errors = validate_stale_recovery_settings(
        Settings(autonomous_execution_stale_timeout_minutes=4)
    )
    assert any("at least 5" in e for e in errors)


def test_validate_stale_batch_size():
    errors = validate_stale_recovery_settings(
        Settings(autonomous_execution_stale_recovery_batch_size=0)
    )
    assert any("positive" in e.lower() for e in errors)


def test_startup_rejects_invalid_stale_timeout():
    settings = get_settings()
    bad = settings.model_copy(update={"autonomous_execution_stale_timeout_minutes": 1})
    with pytest.raises(ConfigurationError):
        validate_configuration(bad)


@pytest.mark.asyncio
async def test_stale_executing_action_is_recovered():
    org_id, _, action_id, idem_key = await _seed_action()
    async with AsyncSessionLocal() as db:
        recovered = await reap_stale_executing_actions(db)
        await db.commit()
        action = await db.get(AIAction, action_id)

    assert action_id in recovered
    assert action.status == AIActionStatus.failed
    assert action.executing_at is None
    assert STALE_RECOVERY_ERROR_PREFIX in (action.error or "")
    assert action.idempotency_key == idem_key


@pytest.mark.asyncio
async def test_non_stale_executing_action_is_untouched():
    async with AsyncSessionLocal() as db:
        org = Organization(name="Fresh", slug=f"fresh-{uuid.uuid4().hex[:8]}", demo_mode=True)
        db.add(org)
        await db.flush()
        action = AIAction(
            organization_id=org.id,
            action_type=AIActionType.create_creative,
            description="fresh",
            reason="fresh",
            status=AIActionStatus.executing,
            executing_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        db.add(action)
        await db.commit()
        action_id = action.id

    async with AsyncSessionLocal() as db:
        recovered = await reap_stale_executing_actions(db)
        await db.commit()
        action = await db.get(AIAction, action_id)

    assert action_id not in recovered
    assert action.status == AIActionStatus.executing
    assert action.executing_at is not None


@pytest.mark.asyncio
async def test_concurrent_recovery_only_happens_once():
    org_id, _, action_id, _ = await _seed_action()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=30)

    async def _recover_once():
        async with AsyncSessionLocal() as db:
            result = await recover_stale_action(
                db,
                action_id=action_id,
                organization_id=org_id,
                cutoff=cutoff,
                now=now,
                timeout_minutes=30,
            )
            await db.commit()
            return result is not None

    results = await asyncio.gather(_recover_once(), _recover_once())
    assert sum(1 for r in results if r) == 1

    async with AsyncSessionLocal() as db:
        audits = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.resource_id == str(action_id),
                    AuditLog.action == "ai_action.stale_recovery",
                )
            )
        ).scalars().all()
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_newly_claimed_action_not_incorrectly_recovered():
    org_id, _, action_id, _ = await _seed_action(status=AIActionStatus.approved)
    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_id)
        action.status = AIActionStatus.approved
        action.executing_at = None
        claim = await try_claim_action_for_execution(db, action)
        await db.commit()
    assert claim == "claimed"

    async with AsyncSessionLocal() as db:
        recovered = await reap_stale_executing_actions(db)
        await db.commit()
        action = await db.get(AIAction, action_id)

    assert action_id not in recovered
    assert action.status == AIActionStatus.executing


@pytest.mark.asyncio
async def test_recovery_creates_audit_event():
    org_id, _, action_id, idem_key = await _seed_action()
    async with AsyncSessionLocal() as db:
        await reap_stale_executing_actions(db)
        await db.commit()
        audit = await db.scalar(
            select(AuditLog).where(
                AuditLog.action == "ai_action.stale_recovery",
                AuditLog.resource_id == str(action_id),
            )
        )

    assert audit is not None
    assert audit.organization_id == org_id
    assert audit.details["trigger"] == "stale_recovery"
    assert audit.details["previous_status"] == "EXECUTING"
    assert audit.details["new_status"] == "FAILED"
    assert audit.details["idempotency_key"] == idem_key
    assert "stale_for_seconds" in audit.details


@pytest.mark.asyncio
async def test_recovery_does_not_execute_provider():
    org_id, _, action_id, _ = await _seed_action(demo_mode=False)
    with patch.object(ExecutionEngine, "_dispatch", new_callable=AsyncMock) as mock_dispatch:
        async with AsyncSessionLocal() as db:
            await reap_stale_executing_actions(db)
            await db.commit()
    mock_dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_tenant_isolation_on_recovery():
    org_a, _, action_a, _ = await _seed_action()
    org_b, _, action_b, _ = await _seed_action()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=30)

    async with AsyncSessionLocal() as db:
        wrong_org = await recover_stale_action(
            db,
            action_id=action_a,
            organization_id=org_b,
            cutoff=cutoff,
            now=now,
            timeout_minutes=30,
        )
        await db.commit()

    assert wrong_org is None
    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_a)
    assert action.status == AIActionStatus.executing


@pytest.mark.asyncio
async def test_demo_mode_preserved_after_recovery():
    org_id, _, action_id, _ = await _seed_action(demo_mode=True)
    async with AsyncSessionLocal() as db:
        await reap_stale_executing_actions(db)
        await db.commit()
        action = await db.get(AIAction, action_id)
    assert action.demo_mode is True


@pytest.mark.asyncio
async def test_recovered_action_can_be_reclaimed_from_failed():
    org_id, _, action_id, _ = await _seed_action()
    async with AsyncSessionLocal() as db:
        await reap_stale_executing_actions(db)
        await db.commit()

    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_id)
        claim = await try_claim_action_for_execution(db, action)
        await db.commit()

    assert claim == "claimed"
    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_id)
    assert action.status == AIActionStatus.executing
    assert action.executing_at is not None


@pytest.mark.asyncio
async def test_worker_integration_reaps_stale_actions():
    org_id, _, action_id, _ = await _seed_action()
    await Worker(poll_interval=0.01).run_once()
    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_id)
    assert action.status == AIActionStatus.failed


@pytest.mark.asyncio
async def test_reap_is_bounded(monkeypatch):
    for _ in range(3):
        await _seed_action()
    monkeypatch.setattr(get_settings(), "autonomous_execution_stale_recovery_batch_size", 2, raising=False)
    async with AsyncSessionLocal() as db:
        recovered = await reap_stale_executing_actions(db)
        await db.commit()
    assert len(recovered) == 2


@pytest.mark.asyncio
async def test_executing_at_set_on_claim():
    org_id, client_id, action_id, _ = await _seed_action(status=AIActionStatus.approved)
    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_id)
        action.status = AIActionStatus.approved
        action.executing_at = None
        await try_claim_action_for_execution(db, action)
        await db.commit()
        action = await db.get(AIAction, action_id)
    assert action.executing_at is not None
