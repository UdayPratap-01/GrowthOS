from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.integrations.base import ConnectResult, ConnectionStatus, SyncResult
from app.jobs.queue import JobQueue
from app.jobs.registry import ANALYTICS_SYNC
from app.schemas.jobs import JobAcceptedOut
from app.security.audit import write_audit
from app.services.integration_service import IntegrationService

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("", response_model=list[ConnectionStatus])
async def get_integrations(
    client_id: UUID | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[ConnectionStatus]:
    return await IntegrationService(db).list_statuses(auth.organization_id, client_id)


@router.post("/{provider}/connect", response_model=ConnectResult)
async def connect_integration(
    provider: str,
    client_id: UUID | None = None,
    auth: AuthContext = Depends(require_permission(Permission.integration_connect)),
    db: AsyncSession = Depends(get_db),
) -> ConnectResult:
    result = await IntegrationService(db).connect(
        provider,
        organization_id=auth.organization_id,
        user_id=auth.user_id,
        client_id=client_id,
    )
    await write_audit(
        db,
        action="integration.connect_start",
        organization_id=auth.organization_id,
        user_id=auth.user_id,
        resource_type="integration",
        resource_id=provider,
    )
    return result


@router.get("/{provider}/callback")
async def oauth_callback(
    provider: str,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    frontend = settings.frontend_url.rstrip("/")
    if error:
        return RedirectResponse(
            f"{frontend}/integrations?error={error}&detail={error_description or ''}&provider={provider}"
        )
    if not code or not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing code/state")
    try:
        meta = await IntegrationService(db).callback(provider, code=code, state=state)
        await write_audit(
            db,
            action="integration.connected",
            organization_id=UUID(meta["organization_id"]),
            resource_type="integration",
            resource_id=provider,
            details={"account_label": meta.get("account_label")},
        )
        return RedirectResponse(f"{frontend}/integrations?connected={provider}")
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "callback_failed"
        return RedirectResponse(f"{frontend}/integrations?error=callback_failed&detail={detail}&provider={provider}")


@router.post("/{provider}/disconnect", response_model=ConnectionStatus)
async def disconnect_integration(
    provider: str,
    client_id: UUID | None = None,
    auth: AuthContext = Depends(require_permission(Permission.integration_disconnect)),
    db: AsyncSession = Depends(get_db),
) -> ConnectionStatus:
    result = await IntegrationService(db).disconnect(
        provider, organization_id=auth.organization_id, client_id=client_id
    )
    await write_audit(
        db,
        action="integration.disconnect",
        organization_id=auth.organization_id,
        user_id=auth.user_id,
        resource_type="integration",
        resource_id=provider,
    )
    return result


@router.post("/{provider}/sync", response_model=SyncResult)
async def sync_integration(
    provider: str,
    client_id: UUID | None = Query(default=None),
    # Same permission as the async variant below: a sync spends the tenant's
    # external API quota and rewrites analytics, so it is not a read.
    auth: AuthContext = Depends(require_permission(Permission.integration_connect)),
    db: AsyncSession = Depends(get_db),
) -> SyncResult:
    result = await IntegrationService(db).sync(
        provider, organization_id=auth.organization_id, client_id=client_id
    )
    await write_audit(
        db,
        action="integration.sync",
        organization_id=auth.organization_id,
        user_id=auth.user_id,
        resource_type="integration",
        resource_id=provider,
        details={"success": result.success, "records": result.records_synced},
    )
    return result


@router.post("/{provider}/sync/async", response_model=JobAcceptedOut, status_code=202)
async def sync_integration_async(
    provider: str,
    client_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(require_permission(Permission.integration_connect)),
    db: AsyncSession = Depends(get_db),
) -> JobAcceptedOut:
    """
    Queue an analytics sync.

    A full sync pulls several date ranges from an external API, which is too slow
    to hold a request open for. The dedupe key means a user mashing "Sync" gets
    one job, not five.
    """
    job = await JobQueue(db).enqueue(
        job_type=ANALYTICS_SYNC,
        payload={"provider": provider, "client_id": str(client_id) if client_id else None},
        organization_id=auth.organization_id,
        dedupe_key=f"sync:{auth.organization_id}:{provider}:{client_id or 'all'}",
    )
    await db.commit()
    return JobAcceptedOut(
        job_id=job.id,
        status=job.status.value.upper(),
        poll_url=f"/api/v1/jobs/{job.id}",
        message=f"Sync queued for {provider}.",
    )
