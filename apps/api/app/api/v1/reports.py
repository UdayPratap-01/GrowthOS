from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.queue import JobQueue
from app.jobs.registry import REPORT_GENERATE
from app.schemas.jobs import JobAcceptedOut
from app.storage import StorageError

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.security.limits import report_limit
from app.security.quota import requires_quota
from app.services.usage_service import Metric
from app.schemas.report import ReportGenerateRequest, ReportOut
from app.services.report_service import ReportService

router = APIRouter(prefix="/clients/{client_id}/reports", tags=["reports"])


@router.get("", response_model=list[ReportOut])
async def list_reports(
    client_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[ReportOut]:
    return await ReportService(db).list(auth.organization_id, client_id)


@router.post(
    "/generate",
    response_model=ReportOut,
    dependencies=[Depends(report_limit), Depends(requires_quota(Metric.REPORT_GENERATION))],
)
async def generate_report(
    client_id: UUID,
    data: ReportGenerateRequest | None = None,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> ReportOut:
    period_days = data.period_days if data else 7
    return await ReportService(db).generate(
        auth.organization, auth.user_id, client_id, period_days=period_days
    )


@router.post(
    "/generate/async",
    response_model=JobAcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(report_limit), Depends(requires_quota(Metric.REPORT_GENERATION))],
)
async def generate_report_async(
    client_id: UUID,
    data: ReportGenerateRequest | None = None,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> JobAcceptedOut:
    """
    Hand report generation to a worker.

    Reports call the AI provider and render a PDF, so a large period can take
    long enough to hit a proxy timeout. Callers that cannot wait use this and
    poll `/api/v1/jobs/{job_id}`.
    """
    job = await JobQueue(db).enqueue(
        job_type=REPORT_GENERATE,
        payload={
            "client_id": str(client_id),
            "user_id": str(auth.user_id),
            "period_days": (data.period_days if data else 7),
        },
        organization_id=auth.organization_id,
    )
    await db.commit()
    return JobAcceptedOut(
        job_id=job.id,
        poll_url=f"/api/v1/jobs/{job.id}",
        message="Report generation queued. Poll the job for completion.",
    )


@router.get("/{report_id}", response_model=ReportOut)
async def get_report(
    client_id: UUID,
    report_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> ReportOut:
    return await ReportService(db).get(auth.organization_id, client_id, report_id)


@router.get("/{report_id}/pdf")
async def download_report_pdf(
    client_id: UUID,
    report_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = ReportService(db)
    report = await service.get(auth.organization_id, client_id, report_id)
    try:
        data, media_type, extension = await service.load_export(
            auth.organization_id, client_id, report
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="PDF export not available"
        ) from exc
    except StorageError as exc:
        # The export exists; storage is simply unreachable. A 404 here would tell
        # the user their report is gone.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="STORAGE_UNAVAILABLE"
        ) from exc

    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="growthos-report-{report_id}.{extension}"'
        },
    )
