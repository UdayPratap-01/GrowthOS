from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.db.session import get_db
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


@router.post("/generate", response_model=ReportOut)
async def generate_report(
    client_id: UUID,
    data: ReportGenerateRequest | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> ReportOut:
    period_days = data.period_days if data else 7
    return await ReportService(db).generate(
        auth.organization, auth.user_id, client_id, period_days=period_days
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
):
    service = ReportService(db)
    report = await service.get(auth.organization_id, client_id, report_id)
    if not report.export_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF export not available")
    path = Path(report.export_path)
    if not path.exists():
        # try regenerated conventional path
        path = service.resolve_export_path(auth.organization_id, client_id, report_id)
        if not path.exists():
            txt = path.with_suffix(".txt")
            if txt.exists():
                return FileResponse(txt, filename=f"growthos-report-{report_id}.txt", media_type="text/plain")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export file missing")
    media = "application/pdf" if path.suffix == ".pdf" else "text/plain"
    return FileResponse(path, filename=f"growthos-report-{report_id}{path.suffix}", media_type=media)
