from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.orchestrator import get_orchestrator
from app.models.enums import ActionStatus
from app.models.organization import Organization
from app.models.strategy import Strategy, StrategyAction
from app.schemas.strategy import ActionStatusUpdate, StrategyOut
from app.security.audit import write_audit
from app.services.client_service import ClientService


class StrategyService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.clients = ClientService(db)
        self.orchestrator = get_orchestrator()

    async def list_strategies(self, organization_id: UUID, client_id: UUID) -> list[StrategyOut]:
        result = await self.db.execute(
            select(Strategy)
            .options(selectinload(Strategy.actions))
            .where(Strategy.organization_id == organization_id, Strategy.client_id == client_id)
            .order_by(Strategy.created_at.desc())
        )
        return [StrategyOut.model_validate(s) for s in result.scalars().all()]

    async def generate(self, organization: Organization, user_id: UUID, client_id: UUID, title: str | None) -> StrategyOut:
        context = await self.clients.build_client_context(organization, client_id)
        generated = await self.orchestrator.generate_strategy(context, title=title)
        strategy = Strategy(
            organization_id=organization.id,
            client_id=client_id,
            title=generated.title,
            current_situation=generated.current_situation,
            what_is_happening=generated.what_is_happening,
            key_problems=generated.key_problems,
            opportunities=generated.opportunities,
            strategy_summary=generated.strategy_summary,
            status="active",
            source="ai",
            context_snapshot=context.model_dump(mode="json"),
        )
        self.db.add(strategy)
        await self.db.flush()
        for action in generated.actions:
            self.db.add(
                StrategyAction(
                    organization_id=organization.id,
                    client_id=client_id,
                    strategy_id=strategy.id,
                    action=action.action,
                    channel=action.channel,
                    objective=action.objective,
                    priority=action.priority,
                    estimated_effort=action.estimated_effort,
                    expected_outcome=action.expected_outcome,
                    required_assets=action.required_assets,
                    deadline=action.deadline,
                    status=ActionStatus.pending,
                )
            )
        await write_audit(
            self.db,
            action="strategy.generate",
            organization_id=organization.id,
            user_id=user_id,
            resource_type="strategy",
            resource_id=str(strategy.id),
        )
        await self.db.flush()
        result = await self.db.execute(
            select(Strategy).options(selectinload(Strategy.actions)).where(Strategy.id == strategy.id)
        )
        return StrategyOut.model_validate(result.scalar_one())

    async def update_action_status(
        self, organization_id: UUID, user_id: UUID, client_id: UUID, action_id: UUID, data: ActionStatusUpdate
    ) -> StrategyOut:
        action = await self.db.scalar(
            select(StrategyAction).where(
                StrategyAction.id == action_id,
                StrategyAction.organization_id == organization_id,
                StrategyAction.client_id == client_id,
            )
        )
        if not action:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found")
        action.status = data.status
        await write_audit(
            self.db,
            action=f"strategy_action.{data.status.value}",
            organization_id=organization_id,
            user_id=user_id,
            resource_type="strategy_action",
            resource_id=str(action.id),
        )
        await self.db.flush()
        result = await self.db.execute(
            select(Strategy).options(selectinload(Strategy.actions)).where(Strategy.id == action.strategy_id)
        )
        return StrategyOut.model_validate(result.scalar_one())
