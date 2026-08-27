from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.errors import AnalyticsIngestionError
from app.analytics.ingestion import AnalyticsIngestionService
from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.schemas.analytics import (
    AnalyticsOut,
    AnalyzeEnqueueOut,
    AnalyzeRequest,
    IngestEnqueueOut,
    IngestRequest,
    PerformanceListOut,
    PerformanceRecommendationListOut,
    PerformanceRecommendationOut,
    PerformanceRecommendationStatusUpdate,
    PerformanceRowOut,
)
from app.services.analytics_service import AnalyticsService

router = APIRouter(tags=["analytics"])


@router.get("/analytics", response_model=AnalyticsOut)
async def org_analytics(
    period_days: int = Query(default=30, ge=7, le=90),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsOut:
    return await AnalyticsService(db).get_analytics(
        auth.organization_id, client_id=None, period_days=period_days, demo_mode=auth.demo_mode
    )


@router.get("/clients/{client_id}/analytics", response_model=AnalyticsOut)
async def client_analytics(
    client_id: UUID,
    period_days: int = Query(default=30, ge=7, le=90),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsOut:
    return await AnalyticsService(db).get_analytics(
        auth.organization_id, client_id=client_id, period_days=period_days, demo_mode=auth.demo_mode
    )


@router.get("/analytics/performance", response_model=PerformanceListOut)
async def list_performance(
    client_id: UUID | None = Query(default=None),
    platform: str | None = Query(default=None),
    external_campaign_id: str | None = Query(default=None),
    entity_level: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> PerformanceListOut:
    rows, total = await AnalyticsIngestionService(db).list_performance(
        organization_id=auth.organization_id,
        client_id=client_id,
        platform=platform,
        external_campaign_id=external_campaign_id,
        date_from=date_from,
        date_to=date_to,
        entity_level=entity_level,
        limit=limit,
        offset=offset,
    )
    return PerformanceListOut(
        items=[
            PerformanceRowOut(
                id=row.id,
                organization_id=row.organization_id,
                client_id=row.client_id,
                platform=row.platform,
                entity_level=row.entity_level,
                external_account_id=row.external_account_id,
                external_campaign_id=row.external_campaign_id,
                external_ad_set_id=row.external_ad_set_id,
                external_ad_id=row.external_ad_id,
                date=row.date,
                granularity=row.granularity,
                impressions=row.impressions,
                reach=row.reach,
                clicks=row.clicks,
                spend=row.spend,
                conversions=row.conversions,
                leads=row.leads,
                revenue=row.revenue,
                ctr=row.ctr,
                cpc=row.cpc,
                cpm=row.cpm,
                cpl=row.cpl,
                cpa=row.cpa,
                roas=row.roas,
                currency=row.currency,
                data_source=row.data_source.value if hasattr(row.data_source, "value") else str(row.data_source),
                ingested_at=row.ingested_at,
                provider_metadata=row.provider_metadata or {},
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/analytics/ingest", response_model=IngestEnqueueOut, status_code=status.HTTP_202_ACCEPTED)
async def enqueue_analytics_ingest(
    body: IngestRequest,
    auth: AuthContext = Depends(require_permission(Permission.integration_connect)),
    db: AsyncSession = Depends(get_db),
) -> IngestEnqueueOut:
    service = AnalyticsIngestionService(db)
    try:
        job = await service.enqueue(
            organization_id=auth.organization_id,
            provider=body.provider,
            client_id=body.client_id,
            lookback_days=body.lookback_days,
            entity_level=body.entity_level,
            actor_user_id=auth.user_id,
        )
    except AnalyticsIngestionError as exc:
        status_code = status.HTTP_400_BAD_REQUEST
        if exc.code == "INGESTION_DISABLED":
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        raise HTTPException(status_code=status_code, detail={"code": exc.code, "message": exc.message}) from exc
    await db.commit()
    lookback = body.lookback_days
    if lookback is None:
        from app.core.config import get_settings

        lookback = get_settings().analytics_ingestion_lookback_days
    return IngestEnqueueOut(
        job_id=job.id,
        job_type=job.job_type,
        provider=body.provider.strip().lower(),
        client_id=body.client_id,
        lookback_days=int(lookback),
        dedupe_key=job.dedupe_key,
    )


def _recommendation_out(row) -> PerformanceRecommendationOut:
    return PerformanceRecommendationOut(
        id=row.id,
        organization_id=row.organization_id,
        client_id=row.client_id,
        platform=row.platform,
        entity_level=row.entity_level,
        external_account_id=row.external_account_id,
        external_campaign_id=row.external_campaign_id,
        external_ad_set_id=row.external_ad_set_id,
        external_ad_id=row.external_ad_id,
        recommendation_type=row.recommendation_type,
        severity=row.severity,
        title=row.title,
        explanation=row.explanation,
        evidence=row.evidence or [],
        affected_metrics=row.affected_metrics or [],
        current_values=row.current_values or {},
        comparison_values=row.comparison_values or {},
        percentage_changes=row.percentage_changes or {},
        confidence=row.confidence,
        suggested_action=row.suggested_action or {},
        signal_category=row.signal_category,
        analysis_window_days=row.analysis_window_days,
        window_current_start=row.window_current_start,
        window_current_end=row.window_current_end,
        window_previous_start=row.window_previous_start,
        window_previous_end=row.window_previous_end,
        status=row.status.value if hasattr(row.status, "value") else str(row.status),
        explanation_source=row.explanation_source,
        expires_at=row.expires_at,
        reviewed_at=row.reviewed_at,
        created_at=row.created_at,
    )


@router.post("/analytics/analyze", response_model=AnalyzeEnqueueOut, status_code=status.HTTP_202_ACCEPTED)
async def enqueue_analytics_analyze(
    body: AnalyzeRequest,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> AnalyzeEnqueueOut:
    from app.analytics.intelligence import PerformanceIntelligenceService

    if body.window_days not in {7, 14, 30}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="window_days must be 7, 14, or 30",
        )
    try:
        job = await PerformanceIntelligenceService(db).enqueue(
            organization_id=auth.organization_id,
            client_id=body.client_id,
            window_days=body.window_days,
            platform=body.platform,
            entity_level=body.entity_level,
            actor_user_id=auth.user_id,
            use_ai_explanation=body.use_ai_explanation,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await db.commit()
    return AnalyzeEnqueueOut(
        job_id=job.id,
        job_type=job.job_type,
        window_days=body.window_days,
        client_id=body.client_id,
        platform=body.platform,
        dedupe_key=job.dedupe_key,
    )


@router.get("/analytics/recommendations", response_model=PerformanceRecommendationListOut)
async def list_performance_recommendations(
    client_id: UUID | None = Query(default=None),
    platform: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    recommendation_type: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    window_days: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> PerformanceRecommendationListOut:
    from app.analytics.intelligence import PerformanceIntelligenceService

    rows, total = await PerformanceIntelligenceService(db).list_recommendations(
        organization_id=auth.organization_id,
        client_id=client_id,
        platform=platform,
        severity=severity,
        recommendation_type=recommendation_type,
        status=status_filter,
        window_days=window_days,
        limit=limit,
        offset=offset,
    )
    return PerformanceRecommendationListOut(
        items=[_recommendation_out(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/analytics/recommendations/{recommendation_id}", response_model=PerformanceRecommendationOut)
async def get_performance_recommendation(
    recommendation_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> PerformanceRecommendationOut:
    from app.analytics.intelligence import PerformanceIntelligenceService

    row = await PerformanceIntelligenceService(db).get_recommendation(
        organization_id=auth.organization_id,
        recommendation_id=recommendation_id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RECOMMENDATION_NOT_FOUND")
    return _recommendation_out(row)


@router.patch("/analytics/recommendations/{recommendation_id}", response_model=PerformanceRecommendationOut)
async def update_performance_recommendation_status(
    recommendation_id: UUID,
    body: PerformanceRecommendationStatusUpdate,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> PerformanceRecommendationOut:
    from app.analytics.intelligence import PerformanceIntelligenceService
    from app.models.enums import PerformanceRecommendationStatus

    try:
        new_status = PerformanceRecommendationStatus(body.status.strip().upper())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid status; expected NEW|REVIEWED|APPROVED|REJECTED|EXPIRED",
        ) from exc
    row = await PerformanceIntelligenceService(db).update_status(
        organization_id=auth.organization_id,
        recommendation_id=recommendation_id,
        status=new_status,
        actor_user_id=auth.user_id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RECOMMENDATION_NOT_FOUND")
    await db.commit()
    return _recommendation_out(row)


@router.post("/analytics/recommendations/{recommendation_id}/approve")
async def approve_performance_recommendation(
    recommendation_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.action_approve)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Re-evaluate policy at approval time, then create AIAction via existing ActionService."""
    from app.optimization.closed_loop import ClosedLoopOptimizer

    decision = await ClosedLoopOptimizer(db).approve_recommendation(
        organization_id=auth.organization_id,
        recommendation_id=recommendation_id,
        actor_user_id=auth.user_id,
    )
    await db.commit()
    return decision.to_dict()


@router.post("/analytics/recommendations/{recommendation_id}/reject")
async def reject_performance_recommendation(
    recommendation_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.action_approve)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.optimization.closed_loop import ClosedLoopOptimizer

    row = await ClosedLoopOptimizer(db).reject_recommendation(
        organization_id=auth.organization_id,
        recommendation_id=recommendation_id,
        actor_user_id=auth.user_id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RECOMMENDATION_NOT_FOUND")
    await db.commit()
    return {"id": str(row.id), "status": row.status.value}
