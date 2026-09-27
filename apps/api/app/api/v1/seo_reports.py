"""SEO weekly reports API (M9.16)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.schemas.seo_weekly_report import (
    SeoReportConfigOut,
    SeoReportConfigPatch,
    SeoWeeklyReportDetailOut,
    SeoWeeklyReportGenerateOut,
    SeoWeeklyReportGenerateRequest,
    SeoWeeklyReportListOut,
    SeoWeeklyReportOut,
)
from app.security.limits import seo_report_generate_limit
from app.services.seo_weekly_report_service import DISCLAIMER, SeoWeeklyReportService

router = APIRouter(prefix="/reports", tags=["seo-reports"])


def _report_out(row) -> SeoWeeklyReportOut:
    return SeoWeeklyReportOut.model_validate(row, from_attributes=True)


@router.get("/config", response_model=SeoReportConfigOut)
async def get_report_config(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoReportConfigOut:
    config = await SeoWeeklyReportService(db).get_or_create_config(organization_id=auth.organization_id)
    return SeoReportConfigOut.model_validate(config, from_attributes=True)


@router.patch("/config", response_model=SeoReportConfigOut)
async def update_report_config(
    body: SeoReportConfigPatch,
    auth: AuthContext = Depends(require_permission(Permission.integration_connect)),
    db: AsyncSession = Depends(get_db),
) -> SeoReportConfigOut:
    config = await SeoWeeklyReportService(db).update_config(
        organization_id=auth.organization_id,
        reporting_enabled=body.reporting_enabled,
    )
    await db.commit()
    return SeoReportConfigOut.model_validate(config, from_attributes=True)


@router.get("", response_model=SeoWeeklyReportListOut)
async def list_seo_reports(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> SeoWeeklyReportListOut:
    rows, total = await SeoWeeklyReportService(db).list_reports(
        organization_id=auth.organization_id,
        limit=limit,
        offset=offset,
    )
    return SeoWeeklyReportListOut(
        items=[_report_out(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{report_id}", response_model=SeoWeeklyReportDetailOut)
async def get_seo_report(
    report_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoWeeklyReportDetailOut:
    row = await SeoWeeklyReportService(db).get_report(
        organization_id=auth.organization_id,
        report_id=report_id,
    )
    out = SeoWeeklyReportDetailOut.model_validate(row, from_attributes=True)
    out.disclaimer = DISCLAIMER
    return out


@router.post("/generate", response_model=SeoWeeklyReportGenerateOut, status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(seo_report_generate_limit)])
async def generate_seo_report(
    body: SeoWeeklyReportGenerateRequest,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> SeoWeeklyReportGenerateOut:
    svc = SeoWeeklyReportService(db)
    report = await svc.enqueue_generate(
        organization_id=auth.organization_id,
        period_start=body.period_start,
        period_end=body.period_end,
    )
    await db.commit()
    msg = (
        "Report already completed for this period."
        if report.status == "completed"
        else "Report generation enqueued — read-only aggregation, no SEO actions executed."
    )
    return SeoWeeklyReportGenerateOut(
        report_id=report.id,
        status=report.status,
        message=msg,
        poll_job_id=report.background_job_id,
    )
