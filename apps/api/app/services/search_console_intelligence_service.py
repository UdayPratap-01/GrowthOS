"""Search Console intelligence — tenant-scoped sync, performance, and opportunities."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations.google_oauth import ensure_access_token
from app.integrations.persistence import get_integration_row
from app.models.enums import SearchConsoleOpportunityPriority, SearchConsoleSyncStatus
from app.models.search_console import (
    SearchConsoleOpportunity,
    SearchConsolePerformanceRow,
    SearchConsoleSync,
)
from app.seo.search_console.client import SearchConsoleClient
from app.seo.search_console.comparison import pct_change, position_change
from app.seo.search_console.dates import previous_equivalent_range, resolve_custom_range, resolve_preset_range
from app.seo.search_console.intelligence import detect_opportunities

DISCLAIMER = (
    "Search Console intelligence is based on read-only Google Search Console API data. "
    "It does not guarantee indexing, ranking outcomes, or traffic changes. "
    "Recent days may be incomplete due to Search Console reporting latency."
)


class SearchConsoleIntelligenceService:
    PROVIDER = "google_search_console"

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.settings = get_settings()

    async def list_properties(self, *, organization_id: UUID, client_id: UUID | None = None) -> list[dict]:
        row = await self._integration(organization_id, client_id)
        if not row:
            return []
        sites = (row.config or {}).get("sites") or []
        if not sites and (row.config or {}).get("site_url"):
            sites = [{"siteUrl": row.config["site_url"], "permissionLevel": "unknown"}]
        return [
            {
                "site_url": s.get("siteUrl") or "",
                "permission_level": s.get("permissionLevel"),
                "connected": True,
            }
            for s in sites
            if s.get("siteUrl")
        ]

    async def get_sync(self, *, organization_id: UUID, sync_id: UUID) -> SearchConsoleSync:
        row = await self.db.scalar(
            select(SearchConsoleSync).where(
                SearchConsoleSync.id == sync_id,
                SearchConsoleSync.organization_id == organization_id,
            ).limit(1)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sync not found")
        return row

    async def latest_sync(self, *, organization_id: UUID, client_id: UUID | None = None) -> SearchConsoleSync | None:
        q = select(SearchConsoleSync).where(SearchConsoleSync.organization_id == organization_id)
        if client_id:
            q = q.where(SearchConsoleSync.client_id == client_id)
        q = q.order_by(SearchConsoleSync.created_at.desc()).limit(1)
        return await self.db.scalar(q)

    async def run_sync(
        self,
        *,
        organization_id: UUID,
        client_id: UUID | None = None,
        preset: str = "last_28_days",
        start_date: date | None = None,
        end_date: date | None = None,
        site_url: str | None = None,
        include_comparison: bool = True,
    ) -> SearchConsoleSync:
        row = await self._require_integration(organization_id, client_id)
        resolved_site = site_url or (row.config or {}).get("site_url")
        if not resolved_site:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No Search Console property selected")

        if preset == "custom":
            if not start_date or not end_date:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Custom range requires start_date and end_date")
            current_range = resolve_custom_range(start_date, end_date)
        else:
            current_range = resolve_preset_range(preset)

        compare_range = previous_equivalent_range(current_range) if include_comparison else None
        sync_key = self._sync_key(
            organization_id,
            resolved_site,
            current_range.start,
            current_range.end,
            compare_range.start if compare_range else None,
            compare_range.end if compare_range else None,
        )

        existing = await self.db.scalar(
            select(SearchConsoleSync).where(
                SearchConsoleSync.organization_id == organization_id,
                SearchConsoleSync.sync_key == sync_key,
            ).limit(1)
        )
        if existing and existing.status == SearchConsoleSyncStatus.completed:
            return existing

        sync = existing or SearchConsoleSync(
            organization_id=organization_id,
            client_id=client_id,
            site_url=resolved_site,
            sync_key=sync_key,
            status=SearchConsoleSyncStatus.running,
            preset=preset if preset != "custom" else None,
            requested_start_date=current_range.start,
            requested_end_date=current_range.requested_end,
            effective_start_date=current_range.start,
            effective_end_date=current_range.end,
            compare_start_date=compare_range.start if compare_range else None,
            compare_end_date=compare_range.end if compare_range else None,
            meta={"note": current_range.note, "data_source": "search_console_api"},
        )
        if not existing:
            self.db.add(sync)
        else:
            sync.status = SearchConsoleSyncStatus.running
            sync.error = None
        sync.started_at = datetime.now(timezone.utc)
        await self.db.flush()

        try:
            token = await ensure_access_token(
                self.db, row, organization_id=organization_id, provider=self.PROVIDER, client_id=client_id
            )
            client = SearchConsoleClient(access_token=token, timeout=float(self.settings.gsc_sync_timeout_seconds))
            max_rows = int(self.settings.gsc_sync_max_rows_per_dimension)

            site_totals = await client.query_search_analytics(
                resolved_site,
                start_date=current_range.start.isoformat(),
                end_date=current_range.end.isoformat(),
                dimensions=[],
                row_limit=1,
            )
            if site_totals:
                sync.meta = {
                    **(sync.meta or {}),
                    "totals": site_totals[0],
                    "compare_totals": None,
                }

            current_rows: list[dict] = []
            for dims, dim_type in [(["query"], "query"), (["page"], "page"), (["query", "page"], "query_page")]:
                fetched = await client.fetch_all_rows(
                    resolved_site,
                    start_date=current_range.start.isoformat(),
                    end_date=current_range.end.isoformat(),
                    dimensions=dims,
                    max_rows=max_rows,
                )
                for item in fetched:
                    item["dimension_type"] = dim_type
                current_rows.extend(fetched)

            compare_map: dict[str, dict] = {}
            if compare_range:
                compare_totals = await client.query_search_analytics(
                    resolved_site,
                    start_date=compare_range.start.isoformat(),
                    end_date=compare_range.end.isoformat(),
                    dimensions=[],
                    row_limit=1,
                )
                if compare_totals:
                    sync.meta = {**(sync.meta or {}), "compare_totals": compare_totals[0]}
                for dims, dim_type in [(["query"], "query"), (["page"], "page"), (["query", "page"], "query_page")]:
                    fetched = await client.fetch_all_rows(
                        resolved_site,
                        start_date=compare_range.start.isoformat(),
                        end_date=compare_range.end.isoformat(),
                        dimensions=dims,
                        max_rows=max_rows,
                    )
                    for item in fetched:
                        key = self._row_key(dim_type, item)
                        compare_map[key] = item

            await self.db.execute(delete(SearchConsolePerformanceRow).where(SearchConsolePerformanceRow.sync_id == sync.id))
            await self.db.execute(delete(SearchConsoleOpportunity).where(SearchConsoleOpportunity.sync_id == sync.id))

            merged_for_intel: list[dict] = []
            for item in current_rows:
                dim_type = item["dimension_type"]
                key = self._row_key(dim_type, item)
                prev = compare_map.get(key)
                compare_clicks = float(prev["clicks"]) if prev else None
                compare_impressions = float(prev["impressions"]) if prev else None
                compare_ctr = float(prev["ctr"]) if prev else None
                compare_position = float(prev["position"]) if prev else None
                clicks = float(item.get("clicks") or 0)
                impressions = float(item.get("impressions") or 0)
                ctr = float(item.get("ctr") or 0)
                position = float(item.get("position") or 0)
                delta = {
                    "clicks": pct_change(clicks, compare_clicks),
                    "impressions": pct_change(impressions, compare_impressions),
                    "ctr": pct_change(ctr, compare_ctr),
                    "position": position_change(position, compare_position),
                }
                perf = SearchConsolePerformanceRow(
                    sync_id=sync.id,
                    organization_id=organization_id,
                    site_url=resolved_site,
                    dimension_type=dim_type,
                    query=item.get("query"),
                    page_url=item.get("page"),
                    start_date=current_range.start,
                    end_date=current_range.end,
                    clicks=clicks,
                    impressions=impressions,
                    ctr=ctr,
                    average_position=position,
                    compare_clicks=compare_clicks,
                    compare_impressions=compare_impressions,
                    compare_ctr=compare_ctr,
                    compare_position=compare_position,
                    metrics_delta=delta,
                    row_key=key[:512],
                )
                self.db.add(perf)
                merged_for_intel.append(
                    {
                        "query": item.get("query"),
                        "page": item.get("page"),
                        "clicks": clicks,
                        "impressions": impressions,
                        "ctr": ctr,
                        "position": position,
                        "compare_clicks": compare_clicks,
                        "compare_impressions": compare_impressions,
                        "compare_ctr": compare_ctr,
                        "compare_position": compare_position,
                    }
                )

            opportunities = detect_opportunities(merged_for_intel)
            for opp in opportunities:
                self.db.add(
                    SearchConsoleOpportunity(
                        sync_id=sync.id,
                        organization_id=organization_id,
                        site_url=resolved_site,
                        opportunity_type=opp.opportunity_type,
                        rule_id=opp.rule_id,
                        priority=SearchConsoleOpportunityPriority(opp.priority),
                        status="open",
                        query=opp.query,
                        page_url=opp.page_url,
                        date_range_start=current_range.start,
                        date_range_end=current_range.end,
                        clicks=opp.clicks,
                        impressions=opp.impressions,
                        ctr=opp.ctr,
                        average_position=opp.average_position,
                        comparison_metrics=opp.comparison,
                        evidence=opp.evidence,
                        explanation=opp.explanation,
                        dedupe_key=opp.fingerprint()[:512],
                    )
                )

            sync.status = SearchConsoleSyncStatus.completed
            sync.row_count = len(current_rows)
            sync.opportunity_count = len(opportunities)
            sync.completed_at = datetime.now(timezone.utc)
            sync.error = None
            await self.db.flush()
            return sync
        except Exception as exc:
            sync.status = SearchConsoleSyncStatus.failed
            sync.error = str(exc)[:500]
            sync.completed_at = datetime.now(timezone.utc)
            await self.db.flush()
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Search Console sync failed") from exc

    async def list_performance(
        self,
        *,
        organization_id: UUID,
        sync_id: UUID | None = None,
        dimension_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SearchConsolePerformanceRow]:
        sync = sync_id or (await self._require_latest_sync(organization_id)).id
        await self.get_sync(organization_id=organization_id, sync_id=sync)
        q = select(SearchConsolePerformanceRow).where(
            SearchConsolePerformanceRow.sync_id == sync,
            SearchConsolePerformanceRow.organization_id == organization_id,
        )
        if dimension_type:
            q = q.where(SearchConsolePerformanceRow.dimension_type == dimension_type)
        q = q.order_by(SearchConsolePerformanceRow.impressions.desc()).offset(offset).limit(min(limit, 500))
        return list((await self.db.execute(q)).scalars().all())

    async def list_queries(self, *, organization_id: UUID, sync_id: UUID | None = None, limit: int = 50, offset: int = 0):
        return await self.list_performance(
            organization_id=organization_id,
            sync_id=sync_id,
            dimension_type="query",
            limit=limit,
            offset=offset,
        )

    async def list_pages(self, *, organization_id: UUID, sync_id: UUID | None = None, limit: int = 50, offset: int = 0):
        return await self.list_performance(
            organization_id=organization_id,
            sync_id=sync_id,
            dimension_type="page",
            limit=limit,
            offset=offset,
        )

    async def list_opportunities(
        self,
        *,
        organization_id: UUID,
        sync_id: UUID | None = None,
        priority: str | None = None,
        opportunity_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SearchConsoleOpportunity]:
        sync = sync_id or (await self._require_latest_sync(organization_id)).id
        await self.get_sync(organization_id=organization_id, sync_id=sync)
        q = select(SearchConsoleOpportunity).where(
            SearchConsoleOpportunity.sync_id == sync,
            SearchConsoleOpportunity.organization_id == organization_id,
        )
        if priority:
            q = q.where(SearchConsoleOpportunity.priority == priority.lower())
        if opportunity_type:
            q = q.where(SearchConsoleOpportunity.opportunity_type == opportunity_type)
        q = q.order_by(SearchConsoleOpportunity.impressions.desc()).offset(offset).limit(min(limit, 500))
        return list((await self.db.execute(q)).scalars().all())

    async def summary(self, *, organization_id: UUID, client_id: UUID | None = None) -> dict:
        row = await self._integration(organization_id, client_id)
        connected = bool(row and row.status == "connected" and row.secret_ref)
        latest = await self.latest_sync(organization_id=organization_id, client_id=client_id)
        totals = {"clicks": 0.0, "impressions": 0.0, "ctr": 0.0}
        by_priority: dict[str, int] = {}
        opportunity_count = 0
        if latest and latest.status == SearchConsoleSyncStatus.completed:
            meta_totals = (latest.meta or {}).get("totals") or {}
            totals["clicks"] = float(meta_totals.get("clicks") or 0)
            totals["impressions"] = float(meta_totals.get("impressions") or 0)
            totals["ctr"] = float(meta_totals.get("ctr") or 0)
            totals["compare"] = (latest.meta or {}).get("compare_totals")
            opps = (
                await self.db.execute(
                    select(SearchConsoleOpportunity).where(
                        SearchConsoleOpportunity.sync_id == latest.id,
                        SearchConsoleOpportunity.organization_id == organization_id,
                    )
                )
            ).scalars().all()
            opportunity_count = len(opps)
            by_priority = dict(Counter(str(o.priority.value if hasattr(o.priority, "value") else o.priority) for o in opps))
        return {
            "site_url": (row.config or {}).get("site_url") if row else None,
            "connected": connected,
            "last_sync": latest,
            "totals": totals,
            "by_priority": by_priority,
            "opportunity_count": opportunity_count,
            "disclaimer": DISCLAIMER,
        }

    async def _require_latest_sync(self, organization_id: UUID) -> SearchConsoleSync:
        latest = await self.latest_sync(organization_id=organization_id)
        if not latest:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No Search Console sync found")
        return latest

    async def _integration(self, organization_id: UUID, client_id: UUID | None):
        return await get_integration_row(
            self.db, organization_id=organization_id, provider=self.PROVIDER, client_id=client_id
        )

    async def _require_integration(self, organization_id: UUID, client_id: UUID | None):
        row = await self._integration(organization_id, client_id)
        if not row or row.status != "connected" or not row.secret_ref:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Search Console not connected")
        return row

    @staticmethod
    def _sync_key(
        organization_id: UUID,
        site_url: str,
        start: date,
        end: date,
        compare_start: date | None,
        compare_end: date | None,
    ) -> str:
        raw = f"{organization_id}|{site_url}|{start}|{end}|{compare_start}|{compare_end}"
        return hashlib.sha256(raw.encode()).hexdigest()[:64]

    @staticmethod
    def _row_key(dimension_type: str, item: dict) -> str:
        parts = [
            dimension_type,
            str(item.get("query") or ""),
            str(item.get("page") or ""),
            str(item.get("country") or ""),
            str(item.get("device") or ""),
        ]
        return "|".join(parts)
