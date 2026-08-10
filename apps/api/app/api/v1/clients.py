from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.schemas.client import ClientContext, ClientCreate, ClientOut, ClientUpdate
from app.security.quota import requires_quota
from app.services.client_service import ClientService
from app.services.usage_service import Metric

router = APIRouter(prefix="/clients", tags=["clients"])


@router.get("", response_model=list[ClientOut])
async def list_clients(
    q: str | None = None,
    industry: str | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[ClientOut]:
    return await ClientService(db).list_clients(auth.organization_id, q=q, industry=industry)


@router.post(
    "",
    response_model=ClientOut,
    status_code=201,
    dependencies=[Depends(requires_quota(Metric.CLIENT))],
)
async def create_client(
    data: ClientCreate,
    auth: AuthContext = Depends(require_permission(Permission.client_write)),
    db: AsyncSession = Depends(get_db),
) -> ClientOut:
    return await ClientService(db).create_client(auth.organization_id, auth.user_id, data)


@router.get("/{client_id}", response_model=ClientOut)
async def get_client(
    client_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> ClientOut:
    return await ClientService(db).get_client(auth.organization_id, client_id)


@router.patch("/{client_id}", response_model=ClientOut)
async def update_client(
    client_id: UUID,
    data: ClientUpdate,
    auth: AuthContext = Depends(require_permission(Permission.client_write)),
    db: AsyncSession = Depends(get_db),
) -> ClientOut:
    return await ClientService(db).update_client(auth.organization_id, auth.user_id, client_id, data)


@router.delete("/{client_id}", response_model=ClientOut)
async def archive_client(
    client_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.client_write)),
    db: AsyncSession = Depends(get_db),
) -> ClientOut:
    return await ClientService(db).archive_client(auth.organization_id, auth.user_id, client_id)


@router.get("/{client_id}/context", response_model=ClientContext)
async def client_context(
    client_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> ClientContext:
    return await ClientService(db).build_client_context(auth.organization, client_id)
