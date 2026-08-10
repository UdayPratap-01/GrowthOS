"""
AI campaign generation API (P2-A).

Nothing here publishes. The most consequential thing any of these routes does is
record human approval, which moves an internal `review_status` and spends no
advertising money.

Authorization on every route, in layers that each answer a different question:

- `get_current_auth` — who is this, and which organization are they acting in
- `require_permission` — may this role do this at all
- the service's client and campaign lookups — does this record belong to that
  organization (a miss is a 404, which is also the truthful answer: another
  tenant's campaign does not exist for this caller)
- `campaign_generation_limit` / `requires_quota` — is this within the rate budget
  and the plan
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.models.marketing import Campaign
from app.schemas.campaign_generation import (
    ApprovalDecision,
    CampaignGenerateRequest,
    CampaignGeneratorOptionsOut,
    CampaignGenerationRunOut,
    CampaignPackageOut,
    CampaignSummaryOut,
    ConceptAssetOut,
    ConceptRegenerateRequest,
    CreativeConceptOut,
    CreativeVariationOut,
    RejectionDecision,
    VariationGenerateRequest,
)
from app.security.limits import ai_limit, campaign_generation_limit, media_limit
from app.security.quota import requires_quota
from app.services.campaign_generation_service import CampaignGenerationService
from app.services.usage_service import Metric

router = APIRouter(prefix="/campaign-generation", tags=["campaign-generation"])


@router.get("/options", response_model=CampaignGeneratorOptionsOut)
async def generator_options(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> CampaignGeneratorOptionsOut:
    """Platforms, objectives, formats, limits and real provider status."""
    return await CampaignGenerationService(db).options(auth.organization)


@router.post(
    "/generate",
    response_model=CampaignGenerationRunOut,
    status_code=202,
    dependencies=[
        Depends(campaign_generation_limit),
        Depends(requires_quota(Metric.CAMPAIGN_GENERATION)),
    ],
)
async def generate_campaign(
    data: CampaignGenerateRequest,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> CampaignGenerationRunOut:
    """
    Start a generation and return the run to poll.

    202, not 200: the work has been accepted, not finished. The response carries
    the stage list so the UI can render the checklist before the first stage
    completes.
    """
    return await CampaignGenerationService(db).start(auth.organization, auth.user_id, data)


@router.get("/runs", response_model=list[CampaignGenerationRunOut])
async def list_runs(
    client_id: UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[CampaignGenerationRunOut]:
    return await CampaignGenerationService(db).list_runs(
        auth.organization_id, client_id=client_id, limit=limit
    )


@router.get("/runs/{run_id}", response_model=CampaignGenerationRunOut)
async def get_run(
    run_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> CampaignGenerationRunOut:
    """
    Real progress for one run.

    Media counts are recomputed from the job rows on every read, so "Images 2/3"
    reflects finished work rather than elapsed time.
    """
    return await CampaignGenerationService(db).get_run(auth.organization_id, run_id)


@router.get("/campaigns", response_model=list[CampaignSummaryOut])
async def list_generated_campaigns(
    client_id: UUID | None = Query(default=None),
    review_status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[CampaignSummaryOut]:
    stmt = (
        select(Campaign)
        .where(
            Campaign.organization_id == auth.organization_id,
            Campaign.generated_by_ai.is_(True),
        )
        .order_by(Campaign.created_at.desc())
        .limit(limit)
    )
    if client_id:
        stmt = stmt.where(Campaign.client_id == client_id)
    if review_status:
        stmt = stmt.where(Campaign.review_status == review_status.upper())
    rows = list(await db.scalars(stmt))
    return [
        CampaignSummaryOut.model_validate(
            {
                **{
                    field: getattr(row, field)
                    for field in (
                        "id",
                        "client_id",
                        "name",
                        "platform",
                        "objective",
                        "review_status",
                        "status",
                        "audience",
                        "total_budget",
                        "daily_budget",
                        "monthly_budget",
                        "currency",
                        "generated_by_ai",
                        "created_at",
                    )
                },
                "data_source": getattr(row.data_source, "value", str(row.data_source)),
            }
        )
        for row in rows
    ]


@router.get("/campaigns/{campaign_id}/package", response_model=CampaignPackageOut)
async def campaign_package(
    campaign_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> CampaignPackageOut:
    """Strategy, brief, concepts, media, variations, structure and approval state."""
    return await CampaignGenerationService(db).package(auth.organization, campaign_id)


@router.post("/campaigns/{campaign_id}/approve", response_model=CampaignPackageOut)
async def approve_campaign(
    campaign_id: UUID,
    data: ApprovalDecision | None = None,
    auth: AuthContext = Depends(require_permission(Permission.action_approve)),
    db: AsyncSession = Depends(get_db),
) -> CampaignPackageOut:
    """
    Approve the package. Moves review_status to READY_TO_PUBLISH.

    READY_TO_PUBLISH describes the package, not a platform: publishing is not
    implemented in this phase, so nothing is scheduled and no budget is
    committed anywhere.
    """
    return await CampaignGenerationService(db).approve(
        auth.organization,
        user_id=auth.user_id,
        campaign_id=campaign_id,
        comment=data.comment if data else None,
    )


@router.post("/campaigns/{campaign_id}/reject", response_model=CampaignPackageOut)
async def reject_campaign(
    campaign_id: UUID,
    data: RejectionDecision,
    auth: AuthContext = Depends(require_permission(Permission.action_approve)),
    db: AsyncSession = Depends(get_db),
) -> CampaignPackageOut:
    return await CampaignGenerationService(db).reject(
        auth.organization,
        user_id=auth.user_id,
        campaign_id=campaign_id,
        reason=data.reason,
    )


@router.post(
    "/concepts/{concept_id}/variations",
    response_model=list[CreativeVariationOut],
    dependencies=[Depends(ai_limit), Depends(requires_quota(Metric.AI_REQUEST))],
)
async def create_concept_variations(
    concept_id: UUID,
    data: VariationGenerateRequest | None = None,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> list[CreativeVariationOut]:
    """Vary one concept along one axis at a time."""
    return await CampaignGenerationService(db).create_variations(
        auth.organization,
        concept_id=concept_id,
        request=data or VariationGenerateRequest(),
    )


@router.post(
    "/concepts/{concept_id}/regenerate",
    response_model=list[ConceptAssetOut],
    dependencies=[Depends(media_limit)],
)
async def regenerate_concept_media(
    concept_id: UUID,
    data: ConceptRegenerateRequest | None = None,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> list[ConceptAssetOut]:
    """Re-render this concept's stored prompts. Does not re-run the AI stages."""
    return await CampaignGenerationService(db).regenerate_concept_media(
        auth.organization,
        concept_id=concept_id,
        request=data or ConceptRegenerateRequest(),
    )


@router.post("/concepts/{concept_id}/archive", response_model=CreativeConceptOut)
async def archive_concept(
    concept_id: UUID,
    archived: bool = Query(default=True),
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> CreativeConceptOut:
    return await CampaignGenerationService(db).archive_concept(
        auth.organization, concept_id=concept_id, archived=archived
    )
