"""Provider timeout reconciliation — ambiguous mutations and read-only recovery."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.automation.execution import ExecutionEngine
from app.automation.idempotency import try_claim_action_for_execution
from app.automation.provider_reconciliation import (
    apply_reconciliation_outcome,
    build_reconciliation_metadata,
    enqueue_provider_reconciliation,
    reconcile_action,
)
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.jobs import handlers
from app.jobs.queue import JobQueue
from app.jobs.registry import PROVIDER_RECONCILE
from app.models.ai_ops import AuditLog
from app.models.automation import AIAction, BackgroundJob
from app.models.client import Client
from app.models.enums import AIActionStatus, AIActionType, MemberRole
from app.models.marketing import Campaign
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.publishing import ads_executor as ads_executor_module
from app.publishing import ads_reconciliation as ads_reconciliation_module
from app.publishing.ads_executor import AdsExecutor
from app.publishing.ads_reconciliation import AdsReconciler, ReconciliationResult
from app.publishing.provider_errors import (
    PROVIDER_TIMEOUT_AMBIGUOUS,
    PROVIDER_TRANSPORT_AMBIGUOUS,
    ReconciliationOutcome,
    ReconciliationState,
    is_ambiguous_error_code,
    is_confirmed_failure_code,
)
from app.worker import Worker


async def _seed_pause_action(*, demo_mode: bool = False, external_id: str = "123456789"):
    async with AsyncSessionLocal() as db:
        org = Organization(
            name=f"Recon Org {uuid.uuid4().hex[:6]}",
            slug=f"recon-{uuid.uuid4().hex[:8]}",
            demo_mode=demo_mode,
        )
        user = User(
            email=f"recon-{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=hash_password("pass"),
            full_name="Recon Tester",
        )
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        client = Client(organization_id=org.id, business_name="Recon Client", industry="saas")
        db.add(client)
        await db.flush()
        camp = Campaign(
            organization_id=org.id,
            client_id=client.id,
            name="Recon Camp",
            platform="meta",
            status="active",
            external_id=external_id,
        )
        db.add(camp)
        await db.flush()
        action = AIAction(
            organization_id=org.id,
            client_id=client.id,
            action_type=AIActionType.pause_campaign,
            platform="meta",
            description="pause",
            reason="recon test",
            target_id=str(camp.id),
            status=AIActionStatus.approved,
            requires_approval=False,
            demo_mode=demo_mode,
            idempotency_key=f"recon-{uuid.uuid4().hex[:12]}",
        )
        db.add(action)
        await db.commit()
        return org.id, client.id, action.id, camp.id, action.idempotency_key


def test_timeout_classification():
    assert is_ambiguous_error_code(PROVIDER_TIMEOUT_AMBIGUOUS)
    assert is_ambiguous_error_code(PROVIDER_TRANSPORT_AMBIGUOUS)
    assert not is_ambiguous_error_code("HTTP_400")
    assert is_confirmed_failure_code("INTEGRATION_NOT_CONNECTED")
    assert not is_confirmed_failure_code(PROVIDER_TIMEOUT_AMBIGUOUS)


@pytest.mark.asyncio
async def test_meta_timeout_is_ambiguous(monkeypatch):
    org_id, client_id, action_id, camp_id, _ = await _seed_pause_action(demo_mode=False)

    async def fake_row(*args, **kwargs):
        class Row:
            secret_ref = "enc"
            status = "connected"

        return Row()

    async def fake_timeout(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(ads_executor_module, "get_integration_row", fake_row)
    monkeypatch.setattr(ads_executor_module, "load_tokens", lambda row: {"access_token": "token"})
    monkeypatch.setattr(ads_executor_module.get_settings(), "demo_mode", False)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_timeout)

    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_id)
        camp = await db.get(Campaign, camp_id)
        result = await AdsExecutor(db).execute(action, campaign=camp)
    assert result.ambiguous is True
    assert result.error_code == PROVIDER_TIMEOUT_AMBIGUOUS
    assert result.external_id == "123456789"


@pytest.mark.asyncio
async def test_meta_confirmed_failure_not_ambiguous(monkeypatch):
    org_id, client_id, action_id, camp_id, _ = await _seed_pause_action(demo_mode=False)

    async def fake_row(*args, **kwargs):
        class Row:
            secret_ref = "enc"
            status = "connected"

        return Row()

    class FakeResp:
        status_code = 400
        content = b'{"error":{"message":"bad request"}}'
        text = content.decode()

        def json(self):
            return {"error": {"message": "bad request"}}

    async def fake_post(*args, **kwargs):
        return FakeResp()

    monkeypatch.setattr(ads_executor_module, "get_integration_row", fake_row)
    monkeypatch.setattr(ads_executor_module, "load_tokens", lambda row: {"access_token": "token"})
    monkeypatch.setattr(ads_executor_module.get_settings(), "demo_mode", False)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_id)
        camp = await db.get(Campaign, camp_id)
        result = await AdsExecutor(db).execute(action, campaign=camp)
    assert result.ambiguous is False
    assert result.error_code == "HTTP_400"


@pytest.mark.asyncio
async def test_timeout_does_not_auto_execute_twice(monkeypatch):
    org_id, _, action_id, camp_id, idem = await _seed_pause_action(demo_mode=False)
    dispatch_calls = {"n": 0}

    async def counting_dispatch(self, action, settings):
        dispatch_calls["n"] += 1
        return {
            "confirmed": False,
            "ambiguous": True,
            "error_code": PROVIDER_TIMEOUT_AMBIGUOUS,
            "message": "timeout",
            "external_id": "123456789",
        }

    monkeypatch.setattr(ExecutionEngine, "_dispatch", counting_dispatch)
    monkeypatch.setattr(
        "app.automation.execution.enqueue_provider_reconciliation",
        AsyncMock(),
    )

    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_id)
        out = await ExecutionEngine(db).execute(action, actor_user_id=None)
        await db.commit()
        second = await db.get(AIAction, action_id)
        claim = await try_claim_action_for_execution(db, second)

    assert dispatch_calls["n"] == 1
    assert out.status == AIActionStatus.failed
    assert second.idempotency_key == idem
    assert (out.result or {}).get("reconciliation", {}).get("state") == "PENDING"
    assert claim == "blocked"


@pytest.mark.asyncio
async def test_successful_reconciliation_completes_action():
    org_id, _, action_id, _, idem = await _seed_pause_action()
    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_id)
        action.status = AIActionStatus.failed
        action.error = "PROVIDER_STATE_UNKNOWN: timeout"
        action.result = {
            "reconciliation": build_reconciliation_metadata(
                provider="meta",
                operation="PAUSE_CAMPAIGN",
                external_id="123456789",
                error_code=PROVIDER_TIMEOUT_AMBIGUOUS,
                platform="meta",
            )
        }
        await db.commit()

        result = ReconciliationResult(
            outcome=ReconciliationOutcome.confirmed_success,
            message="paused",
            provider="meta",
            operation="PAUSE_CAMPAIGN",
            external_id="123456789",
            observed_state={"status": "PAUSED"},
        )
        updated = await apply_reconciliation_outcome(db, action=action, result=result)
        await db.commit()
        reloaded = await db.get(AIAction, action_id)

    assert updated is not None
    assert reloaded.status == AIActionStatus.completed
    assert reloaded.idempotency_key == idem
    assert reloaded.external_id == "123456789"


@pytest.mark.asyncio
async def test_not_applied_reconciliation_allows_retry():
    org_id, _, action_id, _, _ = await _seed_pause_action()
    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_id)
        action.status = AIActionStatus.failed
        action.result = {
            "reconciliation": build_reconciliation_metadata(
                provider="meta",
                operation="PAUSE_CAMPAIGN",
                external_id="123456789",
                error_code=PROVIDER_TIMEOUT_AMBIGUOUS,
                platform="meta",
            )
        }
        await db.commit()

        result = ReconciliationResult(
            outcome=ReconciliationOutcome.confirmed_not_applied,
            message="still active",
            provider="meta",
            operation="PAUSE_CAMPAIGN",
            external_id="123456789",
            observed_state={"status": "ACTIVE"},
        )
        await apply_reconciliation_outcome(db, action=action, result=result)
        await db.commit()
        reloaded = await db.get(AIAction, action_id)
        claim = await try_claim_action_for_execution(db, reloaded)

    assert reloaded.result["reconciliation"]["state"] == "CONFIRMED_NOT_APPLIED"
    assert claim == "claimed"


@pytest.mark.asyncio
async def test_unknown_reconciliation_blocks_retry():
    org_id, _, action_id, _, _ = await _seed_pause_action()
    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_id)
        action.status = AIActionStatus.failed
        action.result = {
            "reconciliation": build_reconciliation_metadata(
                provider="meta",
                operation="PAUSE_CAMPAIGN",
                external_id="123456789",
                error_code=PROVIDER_TIMEOUT_AMBIGUOUS,
                platform="meta",
            )
        }
        await db.commit()

        result = ReconciliationResult(
            outcome=ReconciliationOutcome.unknown,
            message="lookup failed",
            provider="meta",
            operation="PAUSE_CAMPAIGN",
            external_id="123456789",
        )
        await apply_reconciliation_outcome(db, action=action, result=result)
        await db.commit()
        reloaded = await db.get(AIAction, action_id)
        claim = await try_claim_action_for_execution(db, reloaded)

    assert reloaded.result["reconciliation"]["state"] == "UNKNOWN"
    assert claim == "blocked"


@pytest.mark.asyncio
async def test_concurrent_reconciliation_applies_once():
    org_id, _, action_id, _, _ = await _seed_pause_action()
    result = ReconciliationResult(
        outcome=ReconciliationOutcome.confirmed_success,
        message="paused",
        provider="meta",
        operation="PAUSE_CAMPAIGN",
        external_id="123456789",
    )

    async def _apply_once():
        async with AsyncSessionLocal() as db:
            action = await db.get(AIAction, action_id)
            applied = await apply_reconciliation_outcome(db, action=action, result=result)
            await db.commit()
            return applied is not None

    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_id)
        action.status = AIActionStatus.failed
        action.result = {
            "reconciliation": build_reconciliation_metadata(
                provider="meta",
                operation="PAUSE_CAMPAIGN",
                external_id="123456789",
                error_code=PROVIDER_TIMEOUT_AMBIGUOUS,
                platform="meta",
            )
        }
        await db.commit()

    outcomes = await asyncio.gather(_apply_once(), _apply_once())
    assert sum(1 for o in outcomes if o) == 1


@pytest.mark.asyncio
async def test_reconciliation_creates_audit_event():
    org_id, _, action_id, _, _ = await _seed_pause_action()
    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_id)
        action.status = AIActionStatus.failed
        action.result = {
            "reconciliation": build_reconciliation_metadata(
                provider="meta",
                operation="PAUSE_CAMPAIGN",
                external_id="123456789",
                error_code=PROVIDER_TIMEOUT_AMBIGUOUS,
                platform="meta",
            )
        }
        await db.commit()

        await apply_reconciliation_outcome(
            db,
            action=action,
            result=ReconciliationResult(
                outcome=ReconciliationOutcome.confirmed_success,
                message="ok",
                provider="meta",
                operation="PAUSE_CAMPAIGN",
                external_id="123456789",
            ),
        )
        await db.commit()
        audit = await db.scalar(
            select(AuditLog).where(
                AuditLog.action == "ai_action.provider_reconciled",
                AuditLog.resource_id == str(action_id),
            )
        )

    assert audit is not None
    assert audit.details["trigger"] == "reconciliation_job"
    assert audit.details["reconciliation_result"] == "CONFIRMED_SUCCESS"
    assert "access_token" not in str(audit.details)


@pytest.mark.asyncio
async def test_tenant_isolation_on_reconcile():
    org_a, _, action_a, _, _ = await _seed_pause_action()
    org_b, _, _, _, _ = await _seed_pause_action()
    async with AsyncSessionLocal() as db:
        out = await reconcile_action(db, action_id=action_a, organization_id=org_b)
    assert out["skipped"] is True


@pytest.mark.asyncio
async def test_demo_reconciliation_unsupported():
    org_id, _, action_id, camp_id, _ = await _seed_pause_action(demo_mode=True)
    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_id)
        camp = await db.get(Campaign, camp_id)
        result = await AdsReconciler(db).reconcile(action, campaign=camp)
    assert result.outcome == ReconciliationOutcome.unsupported


@pytest.mark.asyncio
async def test_meta_reconciliation_pause_success(monkeypatch):
    org_id, _, action_id, camp_id, _ = await _seed_pause_action(demo_mode=False)

    async def fake_row(*args, **kwargs):
        class Row:
            secret_ref = "enc"
            status = "connected"

        return Row()

    class FakeResp:
        status_code = 200
        content = b'{"status":"PAUSED"}'

        def json(self):
            return {"status": "PAUSED"}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=FakeResp())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(ads_reconciliation_module, "get_integration_row", fake_row)
    monkeypatch.setattr(ads_reconciliation_module, "load_tokens", lambda row: {"access_token": "secret-token"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: mock_client)

    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_id)
        camp = await db.get(Campaign, camp_id)
        result = await AdsReconciler(db).reconcile(action, campaign=camp)

    assert result.outcome == ReconciliationOutcome.confirmed_success
    assert "secret-token" not in str(result.platform_response)


@pytest.mark.asyncio
async def test_unsupported_operation_reconciliation():
    org_id, _, action_id, camp_id, _ = await _seed_pause_action()
    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_id)
        action.action_type = AIActionType.create_campaign
        camp = await db.get(Campaign, camp_id)
        result = await AdsReconciler(db).reconcile(action, campaign=camp)
    assert result.outcome == ReconciliationOutcome.unsupported


@pytest.mark.asyncio
async def test_worker_reconciliation_job(monkeypatch):
    org_id, _, action_id, _, _ = await _seed_pause_action()
    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_id)
        action.status = AIActionStatus.failed
        action.result = {
            "reconciliation": build_reconciliation_metadata(
                provider="meta",
                operation="PAUSE_CAMPAIGN",
                external_id="123456789",
                error_code=PROVIDER_TIMEOUT_AMBIGUOUS,
                platform="meta",
            )
        }
        job = await JobQueue(db).enqueue(
            job_type=PROVIDER_RECONCILE,
            payload={"action_id": str(action_id)},
            organization_id=org_id,
        )
        await db.commit()

    async def fake_reconcile(db, *, action_id, organization_id, trigger="reconciliation_job"):
        return {
            "action_id": str(action_id),
            "outcome": "CONFIRMED_SUCCESS",
            "status": "COMPLETED",
            "reconciliation_state": "CONFIRMED_SUCCESS",
        }

    monkeypatch.setattr(
        "app.automation.provider_reconciliation.reconcile_action",
        fake_reconcile,
    )

    async with AsyncSessionLocal() as db:
        job_row = await db.get(BackgroundJob, job.id)
        result = await handlers.handle_provider_reconcile(db, job_row)
    assert result["outcome"] == "CONFIRMED_SUCCESS"


@pytest.mark.asyncio
async def test_meta_transport_error_is_ambiguous(monkeypatch):
    org_id, _, action_id, camp_id, _ = await _seed_pause_action(demo_mode=False)

    async def fake_row(*args, **kwargs):
        class Row:
            secret_ref = "enc"
            status = "connected"

        return Row()

    async def fake_http_error(*args, **kwargs):
        raise httpx.ConnectError("connection reset")

    monkeypatch.setattr(ads_executor_module, "get_integration_row", fake_row)
    monkeypatch.setattr(ads_executor_module, "load_tokens", lambda row: {"access_token": "token"})
    monkeypatch.setattr(ads_executor_module.get_settings(), "demo_mode", False)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_http_error)

    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_id)
        camp = await db.get(Campaign, camp_id)
        result = await AdsExecutor(db).execute(action, campaign=camp)
    assert result.ambiguous is True
    assert result.error_code == PROVIDER_TRANSPORT_AMBIGUOUS


async def _seed_google_pause_action(*, external_id: str = "9876543210"):
    async with AsyncSessionLocal() as db:
        org = Organization(
            name=f"Google Recon {uuid.uuid4().hex[:6]}",
            slug=f"g-recon-{uuid.uuid4().hex[:8]}",
            demo_mode=False,
        )
        user = User(
            email=f"g-recon-{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=hash_password("pass"),
            full_name="Google Recon",
        )
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        client = Client(organization_id=org.id, business_name="Google Client", industry="saas")
        db.add(client)
        await db.flush()
        camp = Campaign(
            organization_id=org.id,
            client_id=client.id,
            name="Google Camp",
            platform="google_ads",
            status="active",
            external_id=external_id,
            metrics={"customer_id": "1234567890"},
        )
        db.add(camp)
        await db.flush()
        action = AIAction(
            organization_id=org.id,
            client_id=client.id,
            action_type=AIActionType.pause_campaign,
            platform="google_ads",
            description="pause google",
            reason="recon test",
            target_id=str(camp.id),
            status=AIActionStatus.approved,
            requires_approval=False,
            demo_mode=False,
            idempotency_key=f"g-recon-{uuid.uuid4().hex[:12]}",
        )
        db.add(action)
        await db.commit()
        return org.id, client.id, action.id, camp.id


@pytest.mark.asyncio
async def test_google_reconciliation_pause_success(monkeypatch):
    org_id, _, action_id, camp_id = await _seed_google_pause_action()

    async def fake_row(*args, **kwargs):
        class Row:
            secret_ref = "enc"
            status = "connected"

        return Row()

    async def fake_token(*args, **kwargs):
        return "google-access-token"

    class FakeResp:
        status_code = 200
        content = b'{"results":[{"campaign":{"status":"PAUSED"}}]}'

        def json(self):
            return {"results": [{"campaign": {"status": "PAUSED"}}]}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=FakeResp())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(ads_reconciliation_module, "get_integration_row", fake_row)
    monkeypatch.setattr(ads_reconciliation_module, "ensure_access_token", fake_token)
    monkeypatch.setattr(ads_reconciliation_module.get_settings(), "google_ads_developer_token", "dev-token")
    monkeypatch.setattr(ads_reconciliation_module.get_settings(), "google_ads_login_customer_id", "1234567890")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: mock_client)

    async with AsyncSessionLocal() as db:
        action = await db.get(AIAction, action_id)
        camp = await db.get(Campaign, camp_id)
        result = await AdsReconciler(db).reconcile(action, campaign=camp)

    assert result.outcome == ReconciliationOutcome.confirmed_success
    assert "google-access-token" not in str(result.platform_response)
    assert "dev-token" not in str(result.platform_response)


@pytest.mark.asyncio
async def test_enqueue_reconciliation_is_idempotent():
    org_id, _, action_id, _, _ = await _seed_pause_action()
    async with AsyncSessionLocal() as db:
        first = await enqueue_provider_reconciliation(db, action_id=action_id, organization_id=org_id)
        second = await enqueue_provider_reconciliation(db, action_id=action_id, organization_id=org_id)
        await db.commit()
    assert first.id == second.id
