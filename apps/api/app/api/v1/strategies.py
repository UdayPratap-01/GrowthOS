from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.db.session import get_db
from app.schemas.strategy import ActionStatusUpdate, StrategyGenerateRequest, StrategyOut
from app.services.strategy_service import StrategyService

router = APIRouter(prefix="/clients/{client_id}/strategies", tags=["strategies"])


@router.get("", response_model=list[StrategyOut])
async def list_strategies(
    client_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[StrategyOut]:
    return await StrategyService(db).list_strategies(auth.organization_id, client_id)


@router.post("/generate", response_model=StrategyOut)
async def generate_strategy(
    client_id: UUID,
    data: StrategyGenerateRequest | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> StrategyOut:
    title = data.title if data else None
    return await StrategyService(db).generate(auth.organization, auth.user_id, client_id, title)


@router.patch("/actions/{action_id}", response_model=StrategyOut)
async def update_action(
    client_id: UUID,
    action_id: UUID,
    data: ActionStatusUpdate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> StrategyOut:
    return await StrategyService(db).update_action_status(
        auth.organization_id, auth.user_id, client_id, action_id, data
    )
