"""SEO approval/action service (M9.13)."""

from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.action_types import SEO_ACTIONS
from app.models.ai_ops import AuditLog
from app.models.automation import AIAction
from app.models.enums import AIActionStatus, AIActionType, Priority, RiskLevel
from app.models.seo_generated_content import SeoGeneratedContent
from app.models.seo_internal_link import SeoInternalLinkOpportunity
from app.models.seo_onpage_optimization import SeoOnPageFinding
from app.models.seo_schema import SeoSchemaArtifact
from app.schemas.autopilot import AIActionCreate, ActionDecision
from app.schemas.seo_action import (
    SeoActionEligibilityOut,
    SeoActionListOut,
    SeoActionOut,
    SeoActionSummaryOut,
)
from app.security.audit import write_audit
from app.seo.actions.capabilities import resolve_execution_capability
from app.seo.actions.conflicts import find_conflicting_action
from app.seo.actions.eligibility import evaluate_eligibility
from app.seo.actions.payload import artifact_version_stamp, build_execution_payload
from app.seo.actions.types import SeoActionSourceType
from app.services.action_service import ActionService
from app.services.seo_generated_content_service import SeoGeneratedContentService

NO_AUTONOMOUS = (
    "Explicit user approval is required. M9.13 never auto-approves or auto-executes SEO actions."
)


class SeoActionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def propose_internal_link(
        self,
        *,
        organization_id: UUID,
        opportunity_id: UUID,
        user_id: UUID | None,
    ) -> SeoActionOut:
        opp = await self._get_opportunity(organization_id, opportunity_id)
        content = None
        if opp.generated_content_id:
            content = await SeoGeneratedContentService(self.db).get_content(
                organization_id=organization_id, content_id=opp.generated_content_id
            )
        site_root = _site_root(content.target_url if content else opp.source_url)

        eligibility = evaluate_eligibility(
            action_type=AIActionType.seo_apply_internal_link,
            source_type=SeoActionSourceType.internal_link_opportunity,
            source_status=opp.status,
            site_root=site_root,
            source_url=opp.source_url,
            target_url=opp.target_url,
            has_evidence=bool(opp.evidence_refs),
            archived=content is not None and content.status == "archived",
        )
        if not eligibility.eligible:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "NOT_ELIGIBLE", "reasons": eligibility.reasons},
            )

        conflict = await find_conflicting_action(
            self.db,
            organization_id=organization_id,
            action_type=AIActionType.seo_apply_internal_link,
            source_id=opp.id,
        )
        if conflict:
            raise HTTPException(status_code=409, detail="CONFLICTING_ACTION_EXISTS")

        capability = resolve_execution_capability(action_type=AIActionType.seo_apply_internal_link)
        version_key = opp.dedupe_key
        execution_payload = build_execution_payload(
            source_type=SeoActionSourceType.internal_link_opportunity,
            source_id=opp.id,
            capability=capability,
            artifact_version=artifact_version_stamp(updated_at=opp.updated_at, version_key=version_key),
            target={"source_url": opp.source_url, "target_url": opp.target_url},
            changes={
                "anchor_text": opp.anchor_text,
                "anchor_alternatives": opp.anchor_alternatives,
                "opportunity_type": opp.opportunity_type,
            },
            evidence_refs=opp.evidence_refs,
            limitations=list(opp.limitations) + [NO_AUTONOMOUS],
        )
        action = await self._create_action(
            organization_id=organization_id,
            user_id=user_id,
            action_type=AIActionType.seo_apply_internal_link,
            description=f"Apply internal link: {opp.source_url} → {opp.target_url}",
            reason=opp.relationship_reason,
            evidence=opp.evidence_refs,
            payload=execution_payload,
            target_id=str(opp.id),
        )
        return SeoActionOut.model_validate(action)

    async def propose_onpage_finding(
        self,
        *,
        organization_id: UUID,
        finding_id: UUID,
        user_id: UUID | None,
    ) -> SeoActionOut:
        finding = await self._get_finding(organization_id, finding_id)
        content = await SeoGeneratedContentService(self.db).get_content(
            organization_id=organization_id, content_id=finding.generated_content_id
        )
        site_root = _site_root(content.target_url)

        eligibility = evaluate_eligibility(
            action_type=AIActionType.seo_apply_metadata,
            source_type=SeoActionSourceType.onpage_finding,
            source_status=finding.status,
            site_root=site_root,
            has_evidence=bool(finding.evidence_refs),
            archived=content.status == "archived",
        )
        if not eligibility.eligible:
            raise HTTPException(status_code=400, detail={"code": "NOT_ELIGIBLE", "reasons": eligibility.reasons})

        conflict = await find_conflicting_action(
            self.db,
            organization_id=organization_id,
            action_type=AIActionType.seo_apply_metadata,
            source_id=finding.id,
        )
        if conflict:
            raise HTTPException(status_code=409, detail="CONFLICTING_ACTION_EXISTS")

        capability = resolve_execution_capability(action_type=AIActionType.seo_apply_metadata)
        execution_payload = build_execution_payload(
            source_type=SeoActionSourceType.onpage_finding,
            source_id=finding.id,
            capability=capability,
            artifact_version=artifact_version_stamp(updated_at=finding.updated_at, version_key=finding.dedupe_key),
            target={"url": content.target_url, "content_id": str(content.id)},
            changes={
                "finding_type": finding.finding_type,
                "category": finding.category,
                "suggested_change": finding.suggested_change,
                "expected_value": finding.expected_value,
            },
            evidence_refs=finding.evidence_refs,
            limitations=[NO_AUTONOMOUS],
        )
        action = await self._create_action(
            organization_id=organization_id,
            user_id=user_id,
            action_type=AIActionType.seo_apply_metadata,
            description=f"Apply on-page change: {finding.title}",
            reason=finding.rationale,
            evidence=finding.evidence_refs,
            payload=execution_payload,
            target_id=str(finding.id),
        )
        return SeoActionOut.model_validate(action)

    async def propose_schema_artifact(
        self,
        *,
        organization_id: UUID,
        artifact_id: UUID,
        user_id: UUID | None,
    ) -> SeoActionOut:
        artifact = await self._get_schema(organization_id, artifact_id)
        content = await SeoGeneratedContentService(self.db).get_content(
            organization_id=organization_id, content_id=artifact.generated_content_id
        )
        site_root = _site_root(content.target_url)

        eligibility = evaluate_eligibility(
            action_type=AIActionType.seo_apply_schema,
            source_type=SeoActionSourceType.schema_artifact,
            source_status=artifact.status,
            site_root=site_root,
            has_evidence=bool(artifact.evidence_refs),
            archived=content.status == "archived",
        )
        if artifact.validation_status not in {"valid", "warnings"}:
            eligibility.eligible = False
            eligibility.reasons.append(f"INVALID_VALIDATION_STATUS:{artifact.validation_status}")
        if not eligibility.eligible:
            raise HTTPException(status_code=400, detail={"code": "NOT_ELIGIBLE", "reasons": eligibility.reasons})

        conflict = await find_conflicting_action(
            self.db,
            organization_id=organization_id,
            action_type=AIActionType.seo_apply_schema,
            source_id=artifact.id,
        )
        if conflict:
            raise HTTPException(status_code=409, detail="CONFLICTING_ACTION_EXISTS")

        capability = resolve_execution_capability(action_type=AIActionType.seo_apply_schema)
        execution_payload = build_execution_payload(
            source_type=SeoActionSourceType.schema_artifact,
            source_id=artifact.id,
            capability=capability,
            artifact_version=artifact_version_stamp(
                updated_at=artifact.updated_at, version_key=artifact.generation_key
            ),
            target={"url": content.target_url, "schema_type": artifact.schema_type},
            changes={"json_ld": artifact.json_ld},
            evidence_refs=artifact.evidence_refs,
            limitations=list(artifact.limitations) + [NO_AUTONOMOUS],
        )
        action = await self._create_action(
            organization_id=organization_id,
            user_id=user_id,
            action_type=AIActionType.seo_apply_schema,
            description=f"Apply schema ({artifact.schema_type}) for {content.target_url or content.title}",
            reason=f"Validated schema artifact {artifact.validation_status}",
            evidence=artifact.evidence_refs,
            payload=execution_payload,
            target_id=str(artifact.id),
        )
        return SeoActionOut.model_validate(action)

    async def check_eligibility_internal_link(
        self, organization_id: UUID, opportunity_id: UUID
    ) -> SeoActionEligibilityOut:
        opp = await self._get_opportunity(organization_id, opportunity_id)
        content = None
        if opp.generated_content_id:
            content = await SeoGeneratedContentService(self.db).get_content(
                organization_id=organization_id, content_id=opp.generated_content_id
            )
        site_root = _site_root(content.target_url if content else opp.source_url)
        result = evaluate_eligibility(
            action_type=AIActionType.seo_apply_internal_link,
            source_type=SeoActionSourceType.internal_link_opportunity,
            source_status=opp.status,
            site_root=site_root,
            source_url=opp.source_url,
            target_url=opp.target_url,
            has_evidence=bool(opp.evidence_refs),
            archived=content is not None and content.status == "archived",
        )
        return SeoActionEligibilityOut(
            eligible=result.eligible,
            capability=result.capability.value,
            reasons=result.reasons,
            warnings=result.warnings,
        )

    async def list_actions(
        self,
        organization_id: UUID,
        *,
        status_filter: AIActionStatus | None = None,
        action_type: AIActionType | None = None,
        limit: int = 50,
    ) -> SeoActionListOut:
        stmt = (
            select(AIAction)
            .where(
                AIAction.organization_id == organization_id,
                AIAction.action_type.in_(list(SEO_ACTIONS)),
            )
            .order_by(AIAction.created_at.desc())
            .limit(limit)
        )
        if status_filter:
            stmt = stmt.where(AIAction.status == status_filter)
        if action_type:
            stmt = stmt.where(AIAction.action_type == action_type)
        rows = (await self.db.execute(stmt)).scalars().all()
        items = [SeoActionOut.model_validate(r) for r in rows]
        return SeoActionListOut(items=items, total=len(items))

    async def get_action(self, organization_id: UUID, action_id: UUID) -> SeoActionOut:
        row = await self._get_seo_action(organization_id, action_id)
        return SeoActionOut.model_validate(row)

    async def approve(self, organization_id: UUID, action_id: UUID, user_id: UUID, note: str | None) -> SeoActionOut:
        row = await self._get_seo_action(organization_id, action_id)
        out = await ActionService(self.db).approve(
            organization_id, action_id, user_id, ActionDecision(note=note)
        )
        await write_audit(
            self.db,
            organization_id=organization_id,
            user_id=user_id,
            action="seo_action.approved",
            resource_type="seo_action",
            resource_id=str(action_id),
            details={"action_type": row.action_type.value},
        )
        return SeoActionOut.model_validate(out)

    async def reject(self, organization_id: UUID, action_id: UUID, user_id: UUID, note: str | None) -> SeoActionOut:
        row = await self._get_seo_action(organization_id, action_id)
        out = await ActionService(self.db).reject(
            organization_id, action_id, user_id, ActionDecision(note=note)
        )
        await self._sync_source_rejected(row)
        await write_audit(
            self.db,
            organization_id=organization_id,
            user_id=user_id,
            action="seo_action.rejected",
            resource_type="seo_action",
            resource_id=str(action_id),
            details={"note": note},
        )
        return SeoActionOut.model_validate(out)

    async def cancel(self, organization_id: UUID, action_id: UUID, user_id: UUID) -> SeoActionOut:
        await self._get_seo_action(organization_id, action_id)
        out = await ActionService(self.db).cancel(organization_id, action_id, user_id)
        await write_audit(
            self.db,
            organization_id=organization_id,
            user_id=user_id,
            action="seo_action.cancelled",
            resource_type="seo_action",
            resource_id=str(action_id),
            details={},
        )
        return SeoActionOut.model_validate(out)

    async def summary(self, organization_id: UUID) -> SeoActionSummaryOut:
        async def count(st: AIActionStatus | None = None) -> int:
            stmt = select(func.count()).select_from(AIAction).where(
                AIAction.organization_id == organization_id,
                AIAction.action_type.in_(list(SEO_ACTIONS)),
            )
            if st:
                stmt = stmt.where(AIAction.status == st)
            return int(await self.db.scalar(stmt) or 0)

        review_only = 0
        rows = (
            await self.db.execute(
                select(AIAction).where(
                    AIAction.organization_id == organization_id,
                    AIAction.action_type.in_(list(SEO_ACTIONS)),
                )
            )
        ).scalars().all()
        for row in rows:
            if (row.payload or {}).get("capability") == "review_only":
                review_only += 1

        return SeoActionSummaryOut(
            pending=await count(AIActionStatus.pending),
            approved=await count(AIActionStatus.approved),
            completed=await count(AIActionStatus.completed),
            rejected=await count(AIActionStatus.rejected),
            failed=await count(AIActionStatus.failed),
            review_only=review_only,
        )

    async def audit_trail(self, organization_id: UUID, action_id: UUID) -> list[dict]:
        await self._get_seo_action(organization_id, action_id)
        rows = (
            await self.db.execute(
                select(AuditLog)
                .where(
                    AuditLog.organization_id == organization_id,
                    AuditLog.resource_id == str(action_id),
                )
                .order_by(AuditLog.created_at.asc())
            )
        ).scalars().all()
        return [
            {
                "action": r.action,
                "user_id": str(r.user_id) if r.user_id else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "details": r.details or {},
            }
            for r in rows
        ]

    async def _create_action(
        self,
        *,
        organization_id: UUID,
        user_id: UUID | None,
        action_type: AIActionType,
        description: str,
        reason: str,
        evidence: list,
        payload: dict,
        target_id: str,
    ) -> AIAction:
        idem = hashlib.sha256(
            f"{organization_id}|{action_type.value}|{target_id}|{payload.get('artifact_version', {})}".encode()
        ).hexdigest()
        payload = dict(payload)
        payload["idempotency_key"] = idem

        out = await ActionService(self.db).create(
            organization_id,
            AIActionCreate(
                action_type=action_type,
                platform="seo",
                target_id=target_id,
                description=description,
                reason=reason,
                evidence=evidence,
                risk_level=RiskLevel.medium,
                priority=Priority.medium,
                payload=payload,
            ),
            user_id=user_id,
        )
        await write_audit(
            self.db,
            organization_id=organization_id,
            user_id=user_id,
            action="seo_action.proposed",
            resource_type="seo_action",
            resource_id=str(out.id),
            details={"action_type": action_type.value, "target_id": target_id},
        )
        row = await self.db.get(AIAction, out.id)
        assert row is not None
        return row

    async def _get_seo_action(self, organization_id: UUID, action_id: UUID) -> AIAction:
        row = await self.db.scalar(
            select(AIAction).where(
                AIAction.id == action_id,
                AIAction.organization_id == organization_id,
                AIAction.action_type.in_(list(SEO_ACTIONS)),
            )
        )
        if not row:
            raise HTTPException(status_code=404, detail="SEO action not found")
        return row

    async def _get_opportunity(self, organization_id: UUID, opportunity_id: UUID) -> SeoInternalLinkOpportunity:
        row = await self.db.scalar(
            select(SeoInternalLinkOpportunity).where(
                SeoInternalLinkOpportunity.id == opportunity_id,
                SeoInternalLinkOpportunity.organization_id == organization_id,
            )
        )
        if not row:
            raise HTTPException(status_code=404, detail="Opportunity not found")
        return row

    async def _get_finding(self, organization_id: UUID, finding_id: UUID) -> SeoOnPageFinding:
        row = await self.db.scalar(
            select(SeoOnPageFinding).where(
                SeoOnPageFinding.id == finding_id,
                SeoOnPageFinding.organization_id == organization_id,
            )
        )
        if not row:
            raise HTTPException(status_code=404, detail="Finding not found")
        return row

    async def _get_schema(self, organization_id: UUID, artifact_id: UUID) -> SeoSchemaArtifact:
        row = await self.db.scalar(
            select(SeoSchemaArtifact).where(
                SeoSchemaArtifact.id == artifact_id,
                SeoSchemaArtifact.organization_id == organization_id,
            )
        )
        if not row:
            raise HTTPException(status_code=404, detail="Schema artifact not found")
        return row

    async def _sync_source_rejected(self, action: AIAction) -> None:
        payload = action.payload or {}
        source_type = payload.get("source_type")
        source_id = payload.get("source_id")
        if not source_id:
            return
        uid = UUID(source_id)
        if source_type == SeoActionSourceType.internal_link_opportunity.value:
            row = await self.db.get(SeoInternalLinkOpportunity, uid)
            if row:
                row.status = "rejected"
        elif source_type == SeoActionSourceType.onpage_finding.value:
            row = await self.db.get(SeoOnPageFinding, uid)
            if row:
                row.status = "rejected"


def _site_root(url: str | None) -> str | None:
    if not url:
        return None
    from urllib.parse import urlparse

    parsed = urlparse(url if "://" in url else f"https://{url}")
    if not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"
