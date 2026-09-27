"""SEO continuous monitoring API (M9.15)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.schemas.seo_monitoring import (
    SeoMonitoringAlertListOut,
    SeoMonitoringAlertOut,
    SeoMonitoringConfigOut,
    SeoMonitoringConfigPatch,
    SeoMonitoringManualRunOut,
    SeoMonitoringRunListOut,
    SeoMonitoringRunOut,
    SeoMonitoringStatusOut,
)
from app.security.limits import seo_monitor_manual_limit
from app.services.seo_monitoring_service import SeoMonitoringService

router = APIRouter(prefix="/monitoring", tags=["seo-monitoring"])


def _config_out(row) -> SeoMonitoringConfigOut:
    return SeoMonitoringConfigOut.model_validate(row, from_attributes=True)


def _run_out(row) -> SeoMonitoringRunOut:
    return SeoMonitoringRunOut.model_validate(row, from_attributes=True)


def _alert_out(row) -> SeoMonitoringAlertOut:
    return SeoMonitoringAlertOut.model_validate(row, from_attributes=True)


@router.get("", response_model=SeoMonitoringStatusOut)
async def get_monitoring_status(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoMonitoringStatusOut:
    status_data = await SeoMonitoringService(db).get_status(organization_id=auth.organization_id)
    last_run = status_data["last_run"]
    return SeoMonitoringStatusOut(
        config=_config_out(status_data["config"]),
        open_alerts=status_data["open_alerts"],
        scheduler_enabled=status_data["scheduler_enabled"],
        next_scheduled_run=status_data["next_scheduled_run"],
        last_run_status=str(last_run.status) if last_run else None,
        last_run_at=last_run.completed_at or last_run.started_at if last_run else None,
        disclaimer=status_data["disclaimer"],
    )


@router.patch("", response_model=SeoMonitoringConfigOut)
async def update_monitoring_config(
    body: SeoMonitoringConfigPatch,
    auth: AuthContext = Depends(require_permission(Permission.integration_connect)),
    db: AsyncSession = Depends(get_db),
) -> SeoMonitoringConfigOut:
    patch = body.model_dump(exclude_unset=True)
    config = await SeoMonitoringService(db).update_config(
        organization_id=auth.organization_id,
        patch=patch,
    )
    await db.commit()
    return _config_out(config)


@router.post("/run", response_model=SeoMonitoringManualRunOut, status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(seo_monitor_manual_limit)])
async def trigger_manual_monitoring_run(
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> SeoMonitoringManualRunOut:
    job = await SeoMonitoringService(db).enqueue_manual_run(organization_id=auth.organization_id)
    await db.commit()
    return SeoMonitoringManualRunOut(
        job_id=job.id,
        message="Monitoring run enqueued — detects and reports only; does not execute SEO actions.",
    )


@router.get("/runs", response_model=SeoMonitoringRunListOut)
async def list_monitoring_runs(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> SeoMonitoringRunListOut:
    rows, total = await SeoMonitoringService(db).list_runs(
        organization_id=auth.organization_id,
        limit=limit,
        offset=offset,
    )
    return SeoMonitoringRunListOut(
        items=[_run_out(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}", response_model=SeoMonitoringRunOut)
async def get_monitoring_run(
    run_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoMonitoringRunOut:
    row = await SeoMonitoringService(db).get_run(organization_id=auth.organization_id, run_id=run_id)
    return _run_out(row)


@router.get("/alerts", response_model=SeoMonitoringAlertListOut)
async def list_monitoring_alerts(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> SeoMonitoringAlertListOut:
    rows, total = await SeoMonitoringService(db).list_alerts(
        organization_id=auth.organization_id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )
    return SeoMonitoringAlertListOut(
        items=[_alert_out(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/alerts/{alert_id}", response_model=SeoMonitoringAlertOut)
async def get_monitoring_alert(
    alert_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoMonitoringAlertOut:
    row = await SeoMonitoringService(db).get_alert(organization_id=auth.organization_id, alert_id=alert_id)
    return _alert_out(row)


@router.post("/alerts/{alert_id}/acknowledge", response_model=SeoMonitoringAlertOut)
async def acknowledge_monitoring_alert(
    alert_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> SeoMonitoringAlertOut:
    row = await SeoMonitoringService(db).acknowledge_alert(
        organization_id=auth.organization_id,
        alert_id=alert_id,
    )
    await db.commit()
    return _alert_out(row)
