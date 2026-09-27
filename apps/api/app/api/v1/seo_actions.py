"""SEO approval/action API (M9.13)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.models.enums import AIActionStatus, AIActionType
from app.schemas.seo_action import (
    SeoActionAuditOut,
    SeoActionDecision,
    SeoActionEligibilityOut,
    SeoActionListOut,
    SeoActionOut,
    SeoActionProposeRequest,
    SeoActionSummaryOut,
)
from app.security.limits import seo_action_propose_limit
from app.services.seo_action_service import SeoActionService

router = APIRouter(prefix="/actions", tags=["seo-actions"])


@router.get("", response_model=SeoActionListOut)
async def list_seo_actions(
    status_filter: AIActionStatus | None = Query(default=None, alias="status"),
    action_type: AIActionType | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoActionListOut:
    return await SeoActionService(db).list_actions(
        auth.organization_id, status_filter=status_filter, action_type=action_type, limit=limit
    )


@router.get("/summary", response_model=SeoActionSummaryOut)
async def seo_actions_summary(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoActionSummaryOut:
    return await SeoActionService(db).summary(auth.organization_id)


@router.get("/{action_id}", response_model=SeoActionOut)
async def get_seo_action(
    action_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoActionOut:
    return await SeoActionService(db).get_action(auth.organization_id, action_id)


@router.get("/{action_id}/audit", response_model=SeoActionAuditOut)
async def seo_action_audit(
    action_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoActionAuditOut:
    events = await SeoActionService(db).audit_trail(auth.organization_id, action_id)
    return SeoActionAuditOut(action_id=action_id, events=events)


@router.post("/{action_id}/approve", response_model=SeoActionOut)
async def approve_seo_action(
    action_id: UUID,
    body: SeoActionDecision,
    auth: AuthContext = Depends(require_permission(Permission.action_approve)),
    db: AsyncSession = Depends(get_db),
) -> SeoActionOut:
    result = await SeoActionService(db).approve(
        auth.organization_id, action_id, auth.user_id, body.note
    )
    await db.commit()
    return result


@router.post("/{action_id}/reject", response_model=SeoActionOut)
async def reject_seo_action(
    action_id: UUID,
    body: SeoActionDecision,
    auth: AuthContext = Depends(require_permission(Permission.action_approve)),
    db: AsyncSession = Depends(get_db),
) -> SeoActionOut:
    result = await SeoActionService(db).reject(
        auth.organization_id, action_id, auth.user_id, body.note
    )
    await db.commit()
    return result


@router.post("/{action_id}/cancel", response_model=SeoActionOut)
async def cancel_seo_action(
    action_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.action_approve)),
    db: AsyncSession = Depends(get_db),
) -> SeoActionOut:
    result = await SeoActionService(db).cancel(auth.organization_id, action_id, auth.user_id)
    await db.commit()
    return result


@router.post(
    "/propose/internal-link/{opportunity_id}",
    response_model=SeoActionOut,
    dependencies=[Depends(seo_action_propose_limit)],
)
async def propose_internal_link_action(
    opportunity_id: UUID,
    body: SeoActionProposeRequest,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> SeoActionOut:
    result = await SeoActionService(db).propose_internal_link(
        organization_id=auth.organization_id,
        opportunity_id=opportunity_id,
        user_id=auth.user_id,
    )
    await db.commit()
    return result


@router.get(
    "/propose/internal-link/{opportunity_id}/eligibility",
    response_model=SeoActionEligibilityOut,
)
async def internal_link_eligibility(
    opportunity_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoActionEligibilityOut:
    return await SeoActionService(db).check_eligibility_internal_link(
        auth.organization_id, opportunity_id
    )


@router.post(
    "/propose/onpage/{finding_id}",
    response_model=SeoActionOut,
    dependencies=[Depends(seo_action_propose_limit)],
)
async def propose_onpage_action(
    finding_id: UUID,
    body: SeoActionProposeRequest,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> SeoActionOut:
    result = await SeoActionService(db).propose_onpage_finding(
        organization_id=auth.organization_id,
        finding_id=finding_id,
        user_id=auth.user_id,
    )
    await db.commit()
    return result


@router.post(
    "/propose/schema/{artifact_id}",
    response_model=SeoActionOut,
    dependencies=[Depends(seo_action_propose_limit)],
)
async def propose_schema_action(
    artifact_id: UUID,
    body: SeoActionProposeRequest,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> SeoActionOut:
    result = await SeoActionService(db).propose_schema_artifact(
        organization_id=auth.organization_id,
        artifact_id=artifact_id,
        user_id=auth.user_id,
    )
    await db.commit()
    return result
