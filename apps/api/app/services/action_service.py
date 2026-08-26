from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.action_types import get_action_spec
from app.automation.idempotency import (
    build_action_idempotency_key,
    find_action_by_idempotency,
    sanitize_platform_response,
)
from app.automation.execution import ExecutionEngine, RollbackHandler
from app.automation.safety import ActionValidator
from app.core.mode import effective_demo_mode
from app.models.automation import AIAction, CreativeAsset, OptimizationEvent, ScheduledPost
from app.models.enums import AIActionStatus, AutonomyMode
from app.models.marketing import Campaign
from app.models.organization import Organization
from app.schemas.autopilot import (
    AIActionCreate,
    AIActionOut,
    ActionDecision,
    AutopilotSummary,
)
from app.security.audit import write_audit
from app.services.autonomy_service import AutonomyService
from app.services.client_service import ClientService


class ActionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        organization_id: UUID,
        data: AIActionCreate,
        *,
        user_id: UUID | None,
        organization: Organization | None = None,
    ) -> AIActionOut:
        if data.client_id is not None:
            await ClientService(self.db).get_client(organization_id, data.client_id)

        settings = await AutonomyService(self.db).get_effective(organization_id, data.client_id)
        validation = await ActionValidator(self.db).validate(
            organization_id=organization_id,
            settings=settings,
            action_type=data.action_type,
            platform=data.platform,
            estimated_cost=data.estimated_cost,
            client_id=data.client_id,
            payload=data.payload,
        )
        if not validation.ok:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="; ".join(validation.errors))

        idempotency_key = build_action_idempotency_key(
            organization_id=organization_id,
            action_type=data.action_type.value,
            target_id=data.target_id,
            payload=data.payload,
            explicit=(data.payload or {}).get("idempotency_key"),
        )
        existing = await find_action_by_idempotency(
            self.db, organization_id=organization_id, idempotency_key=idempotency_key
        )
        if existing is not None:
            return AIActionOut.model_validate(existing)

        spec = get_action_spec(data.action_type)
        requires_approval = validation.requires_approval
        org = organization or await self.db.get(Organization, organization_id)
        demo = bool(
            data.demo_mode if data.demo_mode is not None else effective_demo_mode(org)
        )

        action = AIAction(
            organization_id=organization_id,
            client_id=data.client_id,
            action_type=data.action_type,
            agent=data.agent,
            platform=data.platform,
            target_id=data.target_id,
            description=data.description,
            reason=data.reason,
            evidence=data.evidence,
            expected_impact=data.expected_impact,
            estimated_cost=data.estimated_cost,
            risk_level=data.risk_level or spec.default_risk,
            priority=data.priority,
            requires_approval=requires_approval,
            status=AIActionStatus.pending,
            payload=data.payload,
            demo_mode=demo,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=48),
            idempotency_key=idempotency_key,
        )
        self.db.add(action)
        try:
            async with self.db.begin_nested():
                await self.db.flush()
        except IntegrityError:
            dup = await find_action_by_idempotency(
                self.db, organization_id=organization_id, idempotency_key=idempotency_key
            )
            if dup is not None:
                return AIActionOut.model_validate(dup)
            raise

        # Auto-execute when automation on, approval not required, and mode is assisted/autonomous
        can_auto = (
            settings.automation_enabled
            and not requires_approval
            and settings.autonomy_mode in {AutonomyMode.autonomous, AutonomyMode.assisted}
        )
        if can_auto:
            action.status = AIActionStatus.approved
            action.approved_at = datetime.now(timezone.utc)
            await self.db.flush()
            action = await ExecutionEngine(self.db).execute(action, actor_user_id=user_id)

        await write_audit(
            self.db,
            organization_id=organization_id,
            user_id=user_id,
            action="ai_action.created",
            resource_type="ai_action",
            resource_id=str(action.id),
            details={"action_type": action.action_type.value, "status": action.status.value},
        )
        await self.db.refresh(action)
        return AIActionOut.model_validate(action)

    async def list(
        self,
        organization_id: UUID,
        *,
        client_id: UUID | None = None,
        status_filter: AIActionStatus | None = None,
        limit: int = 100,
    ) -> list[AIActionOut]:
        stmt = (
            select(AIAction)
            .where(AIAction.organization_id == organization_id)
            .order_by(AIAction.created_at.desc())
            .limit(limit)
        )
        if client_id:
            stmt = stmt.where(AIAction.client_id == client_id)
        if status_filter:
            stmt = stmt.where(AIAction.status == status_filter)
        rows = (await self.db.execute(stmt)).scalars().all()
        return [AIActionOut.model_validate(r) for r in rows]

    async def get(self, organization_id: UUID, action_id: UUID) -> AIAction:
        row = await self.db.scalar(
            select(AIAction).where(AIAction.id == action_id, AIAction.organization_id == organization_id)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found")
        return row

    async def approve(self, organization_id: UUID, action_id: UUID, user_id: UUID, data: ActionDecision) -> AIActionOut:
        action = await self.get(organization_id, action_id)
        if action.status not in {AIActionStatus.pending, AIActionStatus.failed}:
            raise HTTPException(status_code=400, detail=f"Cannot approve action in status {action.status.value}")
        if action.expires_at:
            expires = action.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires < datetime.now(timezone.utc):
                action.status = AIActionStatus.expired
                await self.db.flush()
                raise HTTPException(status_code=400, detail="Action expired")
        action.status = AIActionStatus.approved
        action.approved_by = user_id
        action.approved_at = datetime.now(timezone.utc)
        if data.note:
            payload = dict(action.payload or {})
            payload["approval_note"] = data.note
            action.payload = payload
        await write_audit(
            self.db,
            organization_id=organization_id,
            user_id=user_id,
            action="ai_action.approved",
            resource_type="ai_action",
            resource_id=str(action.id),
            details={},
        )
        await self.db.flush()
        action = await ExecutionEngine(self.db).execute(action, actor_user_id=user_id)
        return AIActionOut.model_validate(action)

    async def reject(self, organization_id: UUID, action_id: UUID, user_id: UUID, data: ActionDecision) -> AIActionOut:
        action = await self.get(organization_id, action_id)
        if action.status != AIActionStatus.pending:
            raise HTTPException(status_code=400, detail="Only pending actions can be rejected")
        action.status = AIActionStatus.rejected
        if data.note:
            payload = dict(action.payload or {})
            payload["rejection_note"] = data.note
            action.payload = payload
        await write_audit(
            self.db,
            organization_id=organization_id,
            user_id=user_id,
            action="ai_action.rejected",
            resource_type="ai_action",
            resource_id=str(action.id),
            details={"note": data.note},
        )
        await self.db.flush()
        await self.db.refresh(action)
        return AIActionOut.model_validate(action)

    async def execute(self, organization_id: UUID, action_id: UUID, user_id: UUID) -> AIActionOut:
        action = await self.get(organization_id, action_id)
        if action.status not in {AIActionStatus.approved, AIActionStatus.failed}:
            raise HTTPException(status_code=400, detail="Action must be approved (or failed for retry)")
        # Never endlessly retry financial ops
        if action.retry_count >= 3 and action.action_type.value.startswith(("UPDATE_BUDGET", "CREATE_")):
            raise HTTPException(status_code=400, detail="Max retries reached for this financial/create action")
        action = await ExecutionEngine(self.db).execute(action, actor_user_id=user_id, force=True)
        return AIActionOut.model_validate(action)

    async def cancel(self, organization_id: UUID, action_id: UUID, user_id: UUID) -> AIActionOut:
        action = await self.get(organization_id, action_id)
        if action.status not in {AIActionStatus.pending, AIActionStatus.approved, AIActionStatus.scheduled}:
            raise HTTPException(status_code=400, detail=f"Cannot cancel action in status {action.status.value}")
        action.status = AIActionStatus.cancelled
        await write_audit(
            self.db,
            organization_id=organization_id,
            user_id=user_id,
            action="ai_action.cancelled",
            resource_type="ai_action",
            resource_id=str(action.id),
            details={},
        )
        await self.db.flush()
        await self.db.refresh(action)
        return AIActionOut.model_validate(action)

    async def rollback(self, organization_id: UUID, action_id: UUID, user_id: UUID) -> dict:
        action = await self.get(organization_id, action_id)
        result = await RollbackHandler(self.db).rollback(action)
        await write_audit(
            self.db,
            organization_id=organization_id,
            user_id=user_id,
            action="ai_action.rollback",
            resource_type="ai_action",
            resource_id=str(action.id),
            details=result,
        )
        return result

    async def summary(self, organization_id: UUID, *, demo_mode: bool) -> AutopilotSummary:
        settings = await AutonomyService(self.db).get_or_create(organization_id, None)
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        async def count(status: AIActionStatus | None = None, today: bool = False) -> int:
            stmt = select(func.count()).select_from(AIAction).where(AIAction.organization_id == organization_id)
            if status:
                stmt = stmt.where(AIAction.status == status)
            if today:
                stmt = stmt.where(AIAction.created_at >= start)
            return int(await self.db.scalar(stmt) or 0)

        pending = await count(AIActionStatus.pending)
        executing = await count(AIActionStatus.executing)
        completed_today = int(
            await self.db.scalar(
                select(func.count()).select_from(AIAction).where(
                    AIAction.organization_id == organization_id,
                    AIAction.status == AIActionStatus.completed,
                    AIAction.executed_at >= start,
                )
            )
            or 0
        )
        failed_today = int(
            await self.db.scalar(
                select(func.count()).select_from(AIAction).where(
                    AIAction.organization_id == organization_id,
                    AIAction.status == AIActionStatus.failed,
                    AIAction.updated_at >= start,
                )
            )
            or 0
        )
        scheduled = int(
            await self.db.scalar(
                select(func.count()).select_from(ScheduledPost).where(
                    ScheduledPost.organization_id == organization_id,
                    ScheduledPost.status.in_(["scheduled", "demo_scheduled"]),
                )
            )
            or 0
        )
        creatives = int(
            await self.db.scalar(
                select(func.count()).select_from(CreativeAsset).where(CreativeAsset.organization_id == organization_id)
            )
            or 0
        )
        opts = int(
            await self.db.scalar(
                select(func.count()).select_from(OptimizationEvent).where(
                    OptimizationEvent.organization_id == organization_id,
                    OptimizationEvent.status == "open",
                )
            )
            or 0
        )
        camps = int(
            await self.db.scalar(
                select(func.count()).select_from(Campaign).where(Campaign.organization_id == organization_id)
            )
            or 0
        )
        return AutopilotSummary(
            autonomy_mode=settings.autonomy_mode,
            automation_enabled=settings.automation_enabled,
            pending_approvals=pending,
            executing=executing,
            completed_today=completed_today,
            failed_today=failed_today,
            scheduled_posts=scheduled,
            creatives_generated=creatives,
            optimizations_open=opts,
            campaigns_monitored=camps,
            demo_mode=demo_mode,
        )
