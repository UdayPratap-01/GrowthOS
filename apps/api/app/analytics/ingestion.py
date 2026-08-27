"""Idempotent upsert of normalized marketing performance rows."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.errors import (
    AnalyticsIngestionError,
    ClientRequired,
    IngestionDisabled,
    UnsupportedProvider,
)
from app.analytics.normalize import NormalizedPerformanceRow
from app.analytics.providers.google_ads import GoogleAdsInsightsFetcher
from app.analytics.providers.meta import MetaInsightsFetcher
from app.automation.idempotency import sanitize_platform_response
from app.core.config import get_settings
from app.jobs.queue import JobQueue
from app.models.client import Client
from app.models.enums import DataSource
from app.models.marketing import MarketingPerformanceDaily
from app.security.audit import write_audit
from app.services.usage_service import Metric, meter

logger = logging.getLogger(__name__)

SUPPORTED_INGEST_PROVIDERS = frozenset({"meta", "google_ads"})


def analytics_ingest_dedupe_key(
    *,
    organization_id: UUID,
    provider: str,
    client_id: UUID | None,
    lookback_days: int,
    as_of: date | None = None,
) -> str:
    day = (as_of or date.today()).isoformat()
    client_part = str(client_id) if client_id else "org"
    return f"analytics-ingest:{organization_id}:{provider}:{client_part}:{day}:lb{lookback_days}"


class AnalyticsIngestionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def enqueue(
        self,
        *,
        organization_id: UUID,
        provider: str,
        client_id: UUID | None,
        lookback_days: int | None = None,
        entity_level: str = "campaign",
        actor_user_id: UUID | None = None,
    ):
        settings = get_settings()
        if not settings.analytics_ingestion_enabled:
            raise IngestionDisabled("Analytics ingestion is disabled")

        provider = (provider or "").strip().lower()
        if provider not in SUPPORTED_INGEST_PROVIDERS:
            raise UnsupportedProvider(f"Unsupported ingestion provider {provider!r}")

        if client_id is not None:
            client = await self.db.get(Client, client_id)
            if client is None or client.organization_id != organization_id:
                raise ClientRequired("client_id does not belong to this organization")

        lookback = lookback_days if lookback_days is not None else settings.analytics_ingestion_lookback_days
        lookback = max(1, min(int(lookback), settings.analytics_ingestion_max_lookback_days))

        job = await JobQueue(self.db).enqueue(
            job_type="analytics.ingest",
            payload={
                "provider": provider,
                "client_id": str(client_id) if client_id else None,
                "lookback_days": lookback,
                "entity_level": entity_level,
                "actor_user_id": str(actor_user_id) if actor_user_id else None,
            },
            organization_id=organization_id,
            dedupe_key=analytics_ingest_dedupe_key(
                organization_id=organization_id,
                provider=provider,
                client_id=client_id,
                lookback_days=lookback,
            ),
            max_attempts=5,
        )
        await write_audit(
            self.db,
            action="analytics.ingestion_enqueued",
            organization_id=organization_id,
            user_id=actor_user_id,
            resource_type="background_job",
            resource_id=str(job.id),
            details={
                "provider": provider,
                "client_id": str(client_id) if client_id else None,
                "lookback_days": lookback,
                "entity_level": entity_level,
            },
        )
        return job

    async def ingest(
        self,
        *,
        organization_id: UUID,
        provider: str,
        client_id: UUID | None,
        lookback_days: int | None = None,
        entity_level: str = "campaign",
        actor_user_id: UUID | None = None,
        trigger: str = "job",
    ) -> dict:
        settings = get_settings()
        if not settings.analytics_ingestion_enabled:
            raise IngestionDisabled("Analytics ingestion is disabled")

        provider = (provider or "").strip().lower()
        if provider not in SUPPORTED_INGEST_PROVIDERS:
            raise UnsupportedProvider(f"Unsupported ingestion provider {provider!r}")

        if client_id is not None:
            client = await self.db.get(Client, client_id)
            if client is None or client.organization_id != organization_id:
                raise ClientRequired("client_id does not belong to this organization")

        lookback = lookback_days if lookback_days is not None else settings.analytics_ingestion_lookback_days
        lookback = max(1, min(int(lookback), settings.analytics_ingestion_max_lookback_days))
        entity_level = (entity_level or "campaign").strip().lower()

        await write_audit(
            self.db,
            action="analytics.ingestion_started",
            organization_id=organization_id,
            user_id=actor_user_id,
            resource_type="analytics_ingestion",
            resource_id=provider,
            details={
                "trigger": trigger,
                "provider": provider,
                "client_id": str(client_id) if client_id else None,
                "lookback_days": lookback,
                "entity_level": entity_level,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

        try:
            rows = await self._fetch_provider_rows(
                provider=provider,
                organization_id=organization_id,
                client_id=client_id,
                lookback_days=lookback,
                entity_level=entity_level,
            )
            upserted = await self.upsert_rows(rows, batch_size=settings.analytics_ingestion_batch_size)
            await meter(
                self.db,
                organization_id=organization_id,
                metric=Metric.INTEGRATION_SYNC,
                quantity=1,
                idempotency_key=(
                    f"analytics-ingest:{organization_id}:{provider}:"
                    f"{client_id or 'org'}:{date.today().isoformat()}:lb{lookback}"
                ),
                client_id=client_id,
                details={"provider": provider, "upserted": upserted, "fetched": len(rows)},
            )
            await write_audit(
                self.db,
                action="analytics.ingestion_completed",
                organization_id=organization_id,
                user_id=actor_user_id,
                resource_type="analytics_ingestion",
                resource_id=provider,
                details={
                    "trigger": trigger,
                    "provider": provider,
                    "client_id": str(client_id) if client_id else None,
                    "lookback_days": lookback,
                    "fetched": len(rows),
                    "upserted": upserted,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
            await self.db.flush()
            return {
                "success": True,
                "provider": provider,
                "fetched": len(rows),
                "upserted": upserted,
                "lookback_days": lookback,
                "entity_level": entity_level,
            }
        except AnalyticsIngestionError as exc:
            await write_audit(
                self.db,
                action="analytics.ingestion_failed",
                organization_id=organization_id,
                user_id=actor_user_id,
                resource_type="analytics_ingestion",
                resource_id=provider,
                details={
                    "trigger": trigger,
                    "provider": provider,
                    "client_id": str(client_id) if client_id else None,
                    "error_code": exc.code,
                    "retryable": exc.retryable,
                    "message": exc.message[:300],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
            await self.db.flush()
            raise

    async def _fetch_provider_rows(
        self,
        *,
        provider: str,
        organization_id: UUID,
        client_id: UUID | None,
        lookback_days: int,
        entity_level: str,
    ) -> list[NormalizedPerformanceRow]:
        if provider == "meta":
            return await MetaInsightsFetcher(self.db).fetch(
                organization_id=organization_id,
                client_id=client_id,
                lookback_days=lookback_days,
                entity_level=entity_level,
            )
        if provider == "google_ads":
            return await GoogleAdsInsightsFetcher(self.db).fetch(
                organization_id=organization_id,
                client_id=client_id,
                lookback_days=lookback_days,
                entity_level=entity_level,
            )
        raise UnsupportedProvider(f"Unsupported ingestion provider {provider!r}")

    async def upsert_rows(
        self,
        rows: list[NormalizedPerformanceRow],
        *,
        batch_size: int = 500,
    ) -> int:
        """Insert or update by natural key. Safe to call repeatedly."""
        if not rows:
            return 0
        now = datetime.now(timezone.utc)
        upserted = 0
        for index, row in enumerate(rows):
            if index and index % max(1, batch_size) == 0:
                await self.db.flush()
            existing = await self.db.scalar(
                select(MarketingPerformanceDaily).where(
                    MarketingPerformanceDaily.organization_id == row.organization_id,
                    MarketingPerformanceDaily.platform == row.platform,
                    MarketingPerformanceDaily.entity_level == row.entity_level,
                    MarketingPerformanceDaily.external_account_id == row.external_account_id,
                    MarketingPerformanceDaily.external_campaign_id == row.external_campaign_id,
                    MarketingPerformanceDaily.external_ad_set_id == row.external_ad_set_id,
                    MarketingPerformanceDaily.external_ad_id == row.external_ad_id,
                    MarketingPerformanceDaily.date == row.date,
                    MarketingPerformanceDaily.granularity == row.granularity,
                )
            )
            derived = row.derived()
            metadata = sanitize_platform_response(row.provider_metadata)
            if existing is None:
                self.db.add(
                    MarketingPerformanceDaily(
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
                        ctr=derived.ctr,
                        cpc=derived.cpc,
                        cpm=derived.cpm,
                        cpl=derived.cpl,
                        cpa=derived.cpa,
                        roas=derived.roas,
                        currency=row.currency or "USD",
                        provider_metadata=metadata,
                        data_source=DataSource.live,
                        ingested_at=now,
                    )
                )
            else:
                # Tenant safety: never update a row belonging to another org
                # (natural key already includes organization_id).
                if existing.organization_id != row.organization_id:
                    continue
                existing.client_id = row.client_id
                existing.impressions = row.impressions
                existing.reach = row.reach
                existing.clicks = row.clicks
                existing.spend = row.spend
                existing.conversions = row.conversions
                existing.leads = row.leads
                existing.revenue = row.revenue
                existing.ctr = derived.ctr
                existing.cpc = derived.cpc
                existing.cpm = derived.cpm
                existing.cpl = derived.cpl
                existing.cpa = derived.cpa
                existing.roas = derived.roas
                existing.currency = row.currency or existing.currency or "USD"
                existing.provider_metadata = metadata
                existing.data_source = DataSource.live
                existing.ingested_at = now
            upserted += 1
        await self.db.flush()
        return upserted

    async def list_performance(
        self,
        *,
        organization_id: UUID,
        client_id: UUID | None = None,
        platform: str | None = None,
        external_campaign_id: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        entity_level: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[MarketingPerformanceDaily], int]:
        from sqlalchemy import func

        filters = [MarketingPerformanceDaily.organization_id == organization_id]
        if client_id is not None:
            client = await self.db.get(Client, client_id)
            if client is None or client.organization_id != organization_id:
                return [], 0
            filters.append(MarketingPerformanceDaily.client_id == client_id)
        if platform:
            filters.append(MarketingPerformanceDaily.platform == platform.strip().lower())
        if external_campaign_id:
            filters.append(MarketingPerformanceDaily.external_campaign_id == external_campaign_id)
        if date_from:
            filters.append(MarketingPerformanceDaily.date >= date_from)
        if date_to:
            filters.append(MarketingPerformanceDaily.date <= date_to)
        if entity_level:
            filters.append(MarketingPerformanceDaily.entity_level == entity_level.strip().lower())

        total = int(
            await self.db.scalar(
                select(func.count()).select_from(MarketingPerformanceDaily).where(*filters)
            )
            or 0
        )
        rows = (
            await self.db.scalars(
                select(MarketingPerformanceDaily)
                .where(*filters)
                .order_by(
                    MarketingPerformanceDaily.date.desc(),
                    MarketingPerformanceDaily.platform.asc(),
                    MarketingPerformanceDaily.external_campaign_id.asc(),
                )
                .offset(max(0, offset))
                .limit(max(1, min(limit, 200)))
            )
        ).all()
        return list(rows), total
