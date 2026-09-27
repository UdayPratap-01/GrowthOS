"""SEO generated content API (M9.9)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.schemas.seo_generated_content import (
    SeoContentGenerateRequest,
    SeoGeneratedContentOut,
    SeoGeneratedContentSourceOut,
)
from app.schemas.seo_onpage_optimizer import SeoOnPageFindingOut, SeoOnPageOptimizationOut
from app.schemas.seo_internal_link import (
    SeoInternalLinkGenerateRequest,
    SeoInternalLinkOpportunityOut,
    SeoInternalLinkReportOut,
    SeoInternalLinkSummaryOut,
)
from app.schemas.seo_schema import SeoSchemaArtifactOut, SeoSchemaFindingOut, SeoSchemaGenerateRequest, SeoSchemaListOut, SeoSchemaValidateRequest
from app.security.limits import (
    ai_limit,
    seo_content_generate_limit,
    seo_internal_link_generate_limit,
    seo_onpage_optimize_limit,
    seo_schema_generate_limit,
)
from app.services.seo_generated_content_service import SeoGeneratedContentService
from app.services.seo_internal_link_service import SeoInternalLinkService
from app.services.seo_onpage_optimizer_service import SeoOnPageOptimizerService
from app.services.seo_schema_service import SeoSchemaService
from app.services.usage_service import Metric
from app.security.quota import requires_quota

router = APIRouter(prefix="/content", tags=["seo-content"])


@router.post(
    "/generate",
    dependencies=[Depends(seo_content_generate_limit), Depends(ai_limit), Depends(requires_quota(Metric.AI_REQUEST))],
)
async def generate_seo_content(
    body: SeoContentGenerateRequest,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await SeoGeneratedContentService(db).generate(
        organization_id=auth.organization_id,
        content_brief_id=body.content_brief_id,
        user_id=auth.user_id,
    )
    await db.commit()
    return result


@router.get("", response_model=list[SeoGeneratedContentOut])
async def list_seo_content(
    content_brief_id: UUID | None = Query(default=None),
    content_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[SeoGeneratedContentOut]:
    rows = await SeoGeneratedContentService(db).list_content(
        organization_id=auth.organization_id,
        content_brief_id=content_brief_id,
        content_type=content_type,
        status_filter=status,
        limit=limit,
        offset=offset,
    )
    return [SeoGeneratedContentOut.model_validate(r) for r in rows]


@router.get("/{content_id}", response_model=SeoGeneratedContentOut)
async def get_seo_content(
    content_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoGeneratedContentOut:
    row = await SeoGeneratedContentService(db).get_content(
        organization_id=auth.organization_id,
        content_id=content_id,
    )
    return SeoGeneratedContentOut.model_validate(row)


@router.get("/{content_id}/source", response_model=SeoGeneratedContentSourceOut)
async def get_seo_content_source(
    content_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoGeneratedContentSourceOut:
    return await SeoGeneratedContentService(db).get_source(
        organization_id=auth.organization_id,
        content_id=content_id,
    )


@router.post("/{content_id}/archive", response_model=SeoGeneratedContentOut)
async def archive_seo_content(
    content_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> SeoGeneratedContentOut:
    row = await SeoGeneratedContentService(db).archive_content(
        organization_id=auth.organization_id,
        content_id=content_id,
    )
    await db.commit()
    return SeoGeneratedContentOut.model_validate(row)


@router.post(
    "/{content_id}/optimize",
    dependencies=[Depends(seo_onpage_optimize_limit), Depends(ai_limit), Depends(requires_quota(Metric.AI_REQUEST))],
)
async def optimize_seo_content(
    content_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await SeoOnPageOptimizerService(db).optimize(
        organization_id=auth.organization_id,
        content_id=content_id,
        user_id=auth.user_id,
    )
    await db.commit()
    return result


@router.get("/{content_id}/optimization", response_model=SeoOnPageOptimizationOut)
async def get_seo_content_optimization(
    content_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoOnPageOptimizationOut:
    return await SeoOnPageOptimizerService(db).get_optimization(
        organization_id=auth.organization_id,
        content_id=content_id,
    )


@router.get("/{content_id}/optimization/findings", response_model=list[SeoOnPageFindingOut])
async def list_seo_content_optimization_findings(
    content_id: UUID,
    category: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[SeoOnPageFindingOut]:
    return await SeoOnPageOptimizerService(db).list_findings(
        organization_id=auth.organization_id,
        content_id=content_id,
        category=category,
        severity=severity,
        limit=limit,
        offset=offset,
    )


@router.get("/{content_id}/optimization/findings/{finding_id}", response_model=SeoOnPageFindingOut)
async def get_seo_content_optimization_finding(
    content_id: UUID,
    finding_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoOnPageFindingOut:
    return await SeoOnPageOptimizerService(db).get_finding(
        organization_id=auth.organization_id,
        content_id=content_id,
        finding_id=finding_id,
    )


@router.post(
    "/{content_id}/schema",
    dependencies=[Depends(seo_schema_generate_limit)],
)
async def generate_seo_schema(
    content_id: UUID,
    body: SeoSchemaGenerateRequest | None = None,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await SeoSchemaService(db).generate(
        organization_id=auth.organization_id,
        content_id=content_id,
        user_id=auth.user_id,
        schema_types=body.schema_types if body else None,
    )
    await db.commit()
    return result


@router.get("/{content_id}/schema", response_model=SeoSchemaListOut)
async def list_seo_schema(
    content_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoSchemaListOut:
    return await SeoSchemaService(db).list_schemas(
        organization_id=auth.organization_id,
        content_id=content_id,
    )


@router.post("/{content_id}/schema/validate")
async def validate_seo_schema(
    content_id: UUID,
    body: SeoSchemaValidateRequest | None = None,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await SeoSchemaService(db).validate(
        organization_id=auth.organization_id,
        content_id=content_id,
        user_id=auth.user_id,
        artifact_id=body.artifact_id if body else None,
    )
    await db.commit()
    return result


@router.get("/{content_id}/schema/{schema_id}", response_model=SeoSchemaArtifactOut)
async def get_seo_schema(
    content_id: UUID,
    schema_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoSchemaArtifactOut:
    return await SeoSchemaService(db).get_schema(
        organization_id=auth.organization_id,
        content_id=content_id,
        schema_id=schema_id,
    )


@router.get("/{content_id}/schema/{schema_id}/findings", response_model=list[SeoSchemaFindingOut])
async def list_seo_schema_findings(
    content_id: UUID,
    schema_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[SeoSchemaFindingOut]:
    return await SeoSchemaService(db).list_findings(
        organization_id=auth.organization_id,
        content_id=content_id,
        schema_id=schema_id,
    )


@router.post(
    "/{content_id}/internal-links",
    dependencies=[Depends(seo_internal_link_generate_limit), Depends(ai_limit), Depends(requires_quota(Metric.AI_REQUEST))],
)
async def generate_seo_internal_links(
    content_id: UUID,
    body: SeoInternalLinkGenerateRequest | None = None,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await SeoInternalLinkService(db).generate(
        organization_id=auth.organization_id,
        content_id=content_id,
        user_id=auth.user_id,
        use_ai=body.use_ai if body else True,
    )
    await db.commit()
    return result


@router.get("/{content_id}/internal-links", response_model=SeoInternalLinkReportOut)
async def get_seo_internal_links(
    content_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoInternalLinkReportOut:
    return await SeoInternalLinkService(db).get_report(
        organization_id=auth.organization_id,
        content_id=content_id,
    )


@router.get("/{content_id}/internal-links/opportunities", response_model=list[SeoInternalLinkOpportunityOut])
async def list_seo_internal_link_opportunities(
    content_id: UUID,
    source_url: str | None = Query(default=None),
    target_url: str | None = Query(default=None),
    opportunity_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[SeoInternalLinkOpportunityOut]:
    return await SeoInternalLinkService(db).list_opportunities(
        organization_id=auth.organization_id,
        content_id=content_id,
        source_url=source_url,
        target_url=target_url,
        opportunity_type=opportunity_type,
        status_filter=status,
        limit=limit,
        offset=offset,
    )


@router.get("/{content_id}/internal-links/opportunities/{opportunity_id}", response_model=SeoInternalLinkOpportunityOut)
async def get_seo_internal_link_opportunity(
    content_id: UUID,
    opportunity_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoInternalLinkOpportunityOut:
    return await SeoInternalLinkService(db).get_opportunity(
        organization_id=auth.organization_id,
        content_id=content_id,
        opportunity_id=opportunity_id,
    )


@router.get("/{content_id}/internal-links/summary", response_model=SeoInternalLinkSummaryOut)
async def get_seo_internal_links_summary(
    content_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoInternalLinkSummaryOut:
    return await SeoInternalLinkService(db).summary(
        organization_id=auth.organization_id,
        content_id=content_id,
    )
