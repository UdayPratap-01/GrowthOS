from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_ops import Integration
from app.security.secrets import get_secret_store


async def get_integration_row(
    db: AsyncSession, *, organization_id: UUID, provider: str, client_id: UUID | None
) -> Integration | None:
    stmt = select(Integration).where(
        Integration.organization_id == organization_id,
        Integration.provider == provider,
    )
    if client_id is None:
        stmt = stmt.where(Integration.client_id.is_(None))
    else:
        stmt = stmt.where(Integration.client_id == client_id)
    return await db.scalar(stmt.limit(1))


#: Providers whose page identifiers are used to route inbound webhooks. A page
#: may only ever belong to one integration row, or an incoming lead cannot be
#: attributed to a tenant.
_PAGE_ROUTED_PROVIDERS = {"meta"}


async def assert_pages_unclaimed(
    db: AsyncSession,
    *,
    provider: str,
    organization_id: UUID,
    client_id: UUID | None,
    config: dict | None,
) -> None:
    """
    Refuse a connection that would make webhook routing ambiguous.

    The webhook path already fails closed on a conflict, but discovering the
    conflict there means the lead is quarantined instead of delivered. Catching
    it at connect time turns a silent data-loss condition into an error the
    person connecting the account can act on.
    """
    if provider not in _PAGE_ROUTED_PROVIDERS or not config:
        return

    from app.services.lead_ingest_service import page_ids_for

    incoming = page_ids_for(Integration(provider=provider, config=config))
    if not incoming:
        return

    rows = (
        (await db.execute(select(Integration).where(Integration.provider == provider)))
        .scalars()
        .all()
    )
    for row in rows:
        if row.organization_id == organization_id and row.client_id == client_id:
            continue  # the row being updated
        clash = incoming & page_ids_for(row)
        if clash:
            from fastapi import HTTPException, status as http_status

            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=(
                    f"PAGE_ALREADY_CONNECTED: page {sorted(clash)[0]} is already connected "
                    "to another account. Disconnect it there before connecting it here."
                ),
            )


async def upsert_integration(
    db: AsyncSession,
    *,
    organization_id: UUID,
    provider: str,
    client_id: UUID | None,
    status: str,
    config: dict | None = None,
    token_payload: dict | None = None,
) -> Integration:
    await assert_pages_unclaimed(
        db,
        provider=provider,
        organization_id=organization_id,
        client_id=client_id,
        config=config,
    )
    row = await get_integration_row(db, organization_id=organization_id, provider=provider, client_id=client_id)
    secret_ref = None
    if token_payload is not None:
        secret_ref = get_secret_store().store(json.dumps(token_payload))
    if row is None:
        row = Integration(
            organization_id=organization_id,
            client_id=client_id,
            provider=provider,
            status=status,
            config=config or {},
            secret_ref=secret_ref,
        )
        db.add(row)
    else:
        row.status = status
        if config is not None:
            merged = dict(row.config or {})
            merged.update(config)
            row.config = merged
        if secret_ref is not None:
            row.secret_ref = secret_ref
    await db.flush()
    await db.refresh(row)
    return row


def load_tokens(row: Integration) -> dict | None:
    if not row.secret_ref:
        return None
    try:
        return json.loads(get_secret_store().retrieve(row.secret_ref))
    except Exception:
        return None


async def mark_sync(
    db: AsyncSession,
    row: Integration,
    *,
    status: str,
    records_synced: int = 0,
    error: str | None = None,
) -> Integration:
    cfg = dict(row.config or {})
    cfg["last_synced_at"] = datetime.now(timezone.utc).isoformat()
    cfg["last_sync_records"] = records_synced
    if error:
        cfg["last_sync_error"] = error
    else:
        cfg.pop("last_sync_error", None)
    row.config = cfg
    row.status = status
    await db.flush()
    await db.refresh(row)
    return row


async def clear_integration_secrets(db: AsyncSession, row: Integration) -> Integration:
    row.secret_ref = None
    row.status = "not_connected"
    cfg = dict(row.config or {})
    for key in (
        "account_label",
        "external_account_id",
        "meta_user_id",
        "ad_accounts",
        "customer_id",
        "customers",
        "discovered_campaigns",
        "last_verification",
        "discovery",
        "discovery_updated_at",
    ):
        cfg.pop(key, None)
    row.config = cfg
    await db.flush()
    await db.refresh(row)
    return row
