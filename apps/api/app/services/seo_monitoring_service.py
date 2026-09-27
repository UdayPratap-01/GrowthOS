"""SEO continuous monitoring service (M9.15)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.queue import JobQueue
from app.jobs.seo_monitor_scheduler import (
    SCHEDULER_ACTOR_USER_ID,
    SEO_MONITOR_CYCLE,
    monitor_cycle_dedupe_key,
    organization_has_inflight_monitor_cycle,
    scheduled_window_start,
)
from app.models.automation import AIAction, BackgroundJob
from app.models.enums import (
    AIActionStatus,
    SearchConsoleOpportunityPriority,
    SeoCrawlStatus,
    SeoFindingSeverity,
    SeoMonitoringAlertSeverity,
    SeoMonitoringAlertStatus,
    SeoMonitoringAlertType,
    SeoMonitoringRunStatus,
    SeoMonitoringRunTrigger,
    SeoMonitoringRunType,
    SearchConsoleSyncStatus,
)
from app.models.keyword_opportunity import KeywordOpportunity
from app.models.search_console import SearchConsoleOpportunity, SearchConsoleSync
from app.models.seo import SeoCrawl, SeoFinding
from app.models.seo_competitor import ContentGap, SeoCompetitor
from app.models.seo_monitoring import (
    SeoMonitoringAlert,
    SeoMonitoringConfig,
    SeoMonitoringRun,
    SeoMonitoringSnapshot,
)
from app.services.search_console_intelligence_service import SearchConsoleIntelligenceService
from app.services.seo_analysis_service import SeoAnalysisService
from app.services.seo_competitor_service import SeoCompetitorService
from app.services.seo_crawl_service import SeoCrawlService

DISCLAIMER = (
    "SEO monitoring detects and reports changes. It does not approve, execute, or publish SEO actions. "
    "Website changes remain behind the M9.13 approval system."
)

MAX_ALERT_URLS = 10
MAX_SUMMARY_LEN = 2000
MAX_TITLE_LEN = 256
ALERT_LIST_LIMIT = 100
RUN_LIST_LIMIT = 50

_UNSAFE = re.compile(r"[<>&\"']")


def _safe_text(value: str | None, *, max_len: int = MAX_SUMMARY_LEN) -> str:
    if not value:
        return ""
    cleaned = _UNSAFE.sub("", str(value))
    return cleaned[:max_len]


class SeoMonitoringService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_or_create_config(self, *, organization_id: UUID) -> SeoMonitoringConfig:
        row = await self.db.scalar(
            select(SeoMonitoringConfig).where(SeoMonitoringConfig.organization_id == organization_id).limit(1)
        )
        if row:
            return row
        row = SeoMonitoringConfig(organization_id=organization_id)
        self.db.add(row)
        await self.db.flush()
        return row

    async def update_config(self, *, organization_id: UUID, patch: dict) -> SeoMonitoringConfig:
        config = await self.get_or_create_config(organization_id=organization_id)
        allowed = {
            "monitoring_enabled",
            "site_root_url",
            "crawl_monitoring_enabled",
            "search_console_monitoring_enabled",
            "competitor_monitoring_enabled",
            "crawl_interval_hours",
            "search_console_interval_hours",
            "competitor_interval_hours",
            "alert_cooldown_hours",
            "click_decline_threshold_pct",
            "impression_decline_threshold_pct",
            "position_change_threshold",
            "high_finding_increase_threshold",
            "max_competitors_per_cycle",
        }
        for key, value in patch.items():
            if key not in allowed:
                continue
            if key == "site_root_url" and value:
                from app.seo.normalize import normalize_url
                from app.seo.ssrf import SsrfError, validate_url_target

                normalized = normalize_url(str(value))
                if not normalized:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid site root URL")
                try:
                    await validate_url_target(normalized)
                except SsrfError as exc:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.code) from exc
                value = normalized
            setattr(config, key, value)
        await self.db.flush()
        return config

    async def get_status(self, *, organization_id: UUID) -> dict:
        config = await self.get_or_create_config(organization_id=organization_id)
        open_alerts = await self.db.scalar(
            select(func.count())
            .select_from(SeoMonitoringAlert)
            .where(
                SeoMonitoringAlert.organization_id == organization_id,
                SeoMonitoringAlert.status.in_(
                    (SeoMonitoringAlertStatus.open.value, SeoMonitoringAlertStatus.acknowledged.value)
                ),
            )
        )
        last_run = await self.db.scalar(
            select(SeoMonitoringRun)
            .where(SeoMonitoringRun.organization_id == organization_id)
            .order_by(SeoMonitoringRun.created_at.desc())
            .limit(1)
        )
        from app.core.config import get_settings

        settings = get_settings()
        next_run = None
        if config.monitoring_enabled and settings.seo_monitor_scheduler_enabled:
            interval = settings.seo_monitor_interval_minutes
            base = config.last_monitor_run_at or datetime.now(timezone.utc)
            next_run = base + timedelta(minutes=interval)

        return {
            "config": config,
            "open_alerts": int(open_alerts or 0),
            "last_run": last_run,
            "next_scheduled_run": next_run,
            "scheduler_enabled": settings.seo_monitor_scheduler_enabled,
            "disclaimer": DISCLAIMER,
        }

    async def enqueue_manual_run(self, *, organization_id: UUID) -> BackgroundJob:
        config = await self.get_or_create_config(organization_id=organization_id)
        if not config.monitoring_enabled:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Monitoring is disabled")

        if await organization_has_inflight_monitor_cycle(self.db, organization_id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Monitoring run already in progress")

        from app.core.config import get_settings

        settings = get_settings()
        window = scheduled_window_start(datetime.now(timezone.utc), settings.seo_monitor_interval_minutes)
        dedupe_key = f"seo-monitor-manual:{organization_id}:{window.isoformat()}"
        existing = await self.db.scalar(
            select(BackgroundJob).where(BackgroundJob.dedupe_key == dedupe_key).limit(1)
        )
        if existing is not None:
            return existing

        return await JobQueue(self.db).enqueue(
            job_type=SEO_MONITOR_CYCLE,
            payload={"window": window.isoformat(), "trigger": "manual"},
            organization_id=organization_id,
            dedupe_key=dedupe_key,
            max_attempts=3,
        )

    async def run_monitor_cycle(self, *, organization_id: UUID, job: BackgroundJob) -> dict:
        config = await self.get_or_create_config(organization_id=organization_id)
        if not config.monitoring_enabled:
            return {"skipped": True, "reason": "MONITORING_DISABLED"}

        trigger_raw = (job.payload or {}).get("trigger", "scheduler")
        trigger = SeoMonitoringRunTrigger.manual if trigger_raw == "manual" else SeoMonitoringRunTrigger.scheduler

        run = SeoMonitoringRun(
            organization_id=organization_id,
            run_type=SeoMonitoringRunType.aggregate,
            trigger=trigger.value,
            status=SeoMonitoringRunStatus.running.value,
            started_at=datetime.now(timezone.utc),
            background_job_id=job.id,
            meta={"trigger": trigger.value},
        )
        self.db.add(run)
        await self.db.flush()

        results: dict = {"run_id": str(run.id)}
        alerts_generated = 0
        changes_detected = 0
        records_evaluated = 0

        try:
            now = datetime.now(timezone.utc)

            if config.crawl_monitoring_enabled and config.site_root_url:
                if self._is_due(config.last_crawl_at, config.crawl_interval_hours, now):
                    crawl_result = await self._schedule_crawl(
                        organization_id=organization_id,
                        config=config,
                        monitoring_run_id=run.id,
                    )
                    results["crawl"] = crawl_result
                    if crawl_result.get("enqueued"):
                        config.last_crawl_at = now
                    records_evaluated += 1

            if config.search_console_monitoring_enabled:
                if self._is_due(config.last_sync_at, config.search_console_interval_hours, now):
                    sync_result = await self._run_search_console_sync(
                        organization_id=organization_id,
                        config=config,
                        monitoring_run_id=run.id,
                    )
                    results["search_console"] = sync_result
                    alerts_generated += int(sync_result.get("alerts_generated", 0))
                    changes_detected += int(sync_result.get("changes_detected", 0))
                    records_evaluated += int(sync_result.get("records_evaluated", 0))
                    if sync_result.get("completed"):
                        config.last_sync_at = now

            if config.competitor_monitoring_enabled:
                if self._is_due(config.last_competitor_at, config.competitor_interval_hours, now):
                    comp_result = await self._schedule_competitor_crawls(
                        organization_id=organization_id,
                        config=config,
                        monitoring_run_id=run.id,
                    )
                    results["competitors"] = comp_result
                    records_evaluated += int(comp_result.get("scheduled", 0))
                    if comp_result.get("scheduled", 0) > 0:
                        config.last_competitor_at = now

            action_alerts = await self._evaluate_pending_actions(organization_id=organization_id, run=run)
            alerts_generated += action_alerts
            records_evaluated += 1

            run.status = SeoMonitoringRunStatus.completed.value
            run.completed_at = datetime.now(timezone.utc)
            run.records_evaluated = records_evaluated
            run.changes_detected = changes_detected
            run.alerts_generated = alerts_generated
            run.meta = {**(run.meta or {}), **results}
            config.last_monitor_run_at = now
            config.last_failure_reason = None
            await self.db.flush()
            return results
        except Exception as exc:
            safe_reason = _safe_text(str(exc), max_len=512)
            run.status = SeoMonitoringRunStatus.failed.value
            run.completed_at = datetime.now(timezone.utc)
            run.failure_reason = safe_reason
            config.last_failure_reason = safe_reason
            await self._create_alert(
                organization_id=organization_id,
                monitoring_run_id=run.id,
                alert_type=SeoMonitoringAlertType.monitoring_job_failed,
                severity=SeoMonitoringAlertSeverity.high,
                title="Monitoring run failed",
                summary=safe_reason,
                dedupe_key=f"monitor-failed:{organization_id}:{run.id}",
                entity_type="monitoring_run",
                entity_id=str(run.id),
            )
            await self.db.flush()
            raise

    async def process_crawl_completion(
        self,
        *,
        organization_id: UUID,
        crawl_id: UUID,
        monitoring_run_id: UUID | None = None,
    ) -> dict:
        config = await self.get_or_create_config(organization_id=organization_id)
        crawl = await self.db.scalar(
            select(SeoCrawl).where(SeoCrawl.id == crawl_id, SeoCrawl.organization_id == organization_id).limit(1)
        )
        if not crawl or crawl.status != SeoCrawlStatus.completed:
            return {"skipped": True}

        previous = await self.db.scalar(
            select(SeoCrawl)
            .where(
                SeoCrawl.organization_id == organization_id,
                SeoCrawl.status == SeoCrawlStatus.completed,
                SeoCrawl.id != crawl_id,
            )
            .order_by(SeoCrawl.completed_at.desc())
            .limit(1)
        )

        alerts = 0
        changes = 0
        if previous:
            comparison = await SeoAnalysisService(self.db).compare(
                organization_id=organization_id,
                crawl_id=previous.id,
                other_crawl_id=crawl_id,
            )
            changes += comparison.get("new_findings", 0) + comparison.get("resolved_findings", 0)
            for item in comparison.get("new", []):
                severity = str(item.get("severity", "")).upper()
                if severity == "HIGH":
                    created = await self._create_alert(
                        organization_id=organization_id,
                        monitoring_run_id=monitoring_run_id,
                        alert_type=SeoMonitoringAlertType.technical_new_high_finding,
                        severity=SeoMonitoringAlertSeverity.high,
                        title=_safe_text(f"New high-severity finding: {item.get('title', item.get('rule_id'))}", max_len=MAX_TITLE_LEN),
                        summary=_safe_text(item.get("title") or item.get("rule_id")),
                        dedupe_key=f"tech-high:{organization_id}:{item.get('rule_id')}:{item.get('url', '')}",
                        entity_type="finding",
                        entity_id=str(item.get("rule_id")),
                        affected_urls=[item.get("url")] if item.get("url") else [],
                        evidence=item,
                    )
                    if created:
                        alerts += 1
            for item in comparison.get("resolved", []):
                await self._resolve_alerts_by_prefix(
                    organization_id=organization_id,
                    dedupe_prefix=f"tech-high:{organization_id}:{item.get('rule_id')}:{item.get('url', '')}",
                )

        high_count = await self.db.scalar(
            select(func.count())
            .select_from(SeoFinding)
            .where(
                SeoFinding.organization_id == organization_id,
                SeoFinding.crawl_id == crawl_id,
                SeoFinding.severity == SeoFindingSeverity.high,
            )
        )
        prev_snapshot = await self._get_snapshot(organization_id, "technical", "high_findings_count")
        prev_count = int((prev_snapshot.metric_value or {}).get("count", 0)) if prev_snapshot else 0
        if high_count and high_count - prev_count >= config.high_finding_increase_threshold:
            created = await self._create_alert(
                organization_id=organization_id,
                monitoring_run_id=monitoring_run_id,
                alert_type=SeoMonitoringAlertType.technical_high_count_increase,
                severity=SeoMonitoringAlertSeverity.high,
                title="High-severity finding count increased",
                summary=f"High findings rose from {prev_count} to {high_count}",
                dedupe_key=f"tech-count:{organization_id}",
                threshold=str(config.high_finding_increase_threshold),
                observed_value=str(high_count),
                previous_value=str(prev_count),
                delta=str(high_count - prev_count),
            )
            if created:
                alerts += 1
        await self._upsert_snapshot(
            organization_id=organization_id,
            snapshot_type="technical",
            snapshot_key="high_findings_count",
            metric_value={"count": int(high_count or 0)},
            source_run_id=str(crawl_id),
        )
        return {"alerts_generated": alerts, "changes_detected": changes}

    async def list_runs(
        self,
        *,
        organization_id: UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[SeoMonitoringRun], int]:
        limit = min(limit, RUN_LIST_LIMIT)
        total = await self.db.scalar(
            select(func.count())
            .select_from(SeoMonitoringRun)
            .where(SeoMonitoringRun.organization_id == organization_id)
        )
        rows = (
            await self.db.execute(
                select(SeoMonitoringRun)
                .where(SeoMonitoringRun.organization_id == organization_id)
                .order_by(SeoMonitoringRun.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        return list(rows), int(total or 0)

    async def get_run(self, *, organization_id: UUID, run_id: UUID) -> SeoMonitoringRun:
        row = await self.db.scalar(
            select(SeoMonitoringRun)
            .where(SeoMonitoringRun.id == run_id, SeoMonitoringRun.organization_id == organization_id)
            .limit(1)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Monitoring run not found")
        return row

    async def list_alerts(
        self,
        *,
        organization_id: UUID,
        status_filter: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[SeoMonitoringAlert], int]:
        limit = min(limit, ALERT_LIST_LIMIT)
        q = select(SeoMonitoringAlert).where(SeoMonitoringAlert.organization_id == organization_id)
        count_q = select(func.count()).select_from(SeoMonitoringAlert).where(
            SeoMonitoringAlert.organization_id == organization_id
        )
        if status_filter:
            q = q.where(SeoMonitoringAlert.status == status_filter)
            count_q = count_q.where(SeoMonitoringAlert.status == status_filter)
        total = await self.db.scalar(count_q)
        rows = (
            await self.db.execute(q.order_by(SeoMonitoringAlert.created_at.desc()).offset(offset).limit(limit))
        ).scalars().all()
        return list(rows), int(total or 0)

    async def get_alert(self, *, organization_id: UUID, alert_id: UUID) -> SeoMonitoringAlert:
        row = await self.db.scalar(
            select(SeoMonitoringAlert)
            .where(SeoMonitoringAlert.id == alert_id, SeoMonitoringAlert.organization_id == organization_id)
            .limit(1)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
        return row

    async def acknowledge_alert(self, *, organization_id: UUID, alert_id: UUID) -> SeoMonitoringAlert:
        alert = await self.get_alert(organization_id=organization_id, alert_id=alert_id)
        if alert.status == SeoMonitoringAlertStatus.resolved.value:
            return alert
        alert.status = SeoMonitoringAlertStatus.acknowledged.value
        alert.acknowledged_at = datetime.now(timezone.utc)
        await self.db.flush()
        return alert

    def _is_due(self, last_at: datetime | None, interval_hours: int, now: datetime) -> bool:
        if last_at is None:
            return True
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)
        return now >= last_at + timedelta(hours=max(interval_hours, 1))

    async def _schedule_crawl(
        self,
        *,
        organization_id: UUID,
        config: SeoMonitoringConfig,
        monitoring_run_id: UUID,
    ) -> dict:
        if not config.site_root_url:
            return {"skipped": True, "reason": "NO_SITE_ROOT"}
        crawl = await SeoCrawlService(self.db).create_crawl(
            organization_id=organization_id,
            user_id=SCHEDULER_ACTOR_USER_ID,
            root_url=config.site_root_url,
            config={"max_pages": 50, "max_depth": 3},
        )
        job = await self.db.scalar(
            select(BackgroundJob).where(BackgroundJob.id == crawl.job_id).limit(1)
        )
        if job:
            job.payload = {**(job.payload or {}), "monitoring_run_id": str(monitoring_run_id)}
            await self.db.flush()
        return {"enqueued": True, "crawl_id": str(crawl.id)}

    async def _run_search_console_sync(
        self,
        *,
        organization_id: UUID,
        config: SeoMonitoringConfig,
        monitoring_run_id: UUID,
    ) -> dict:
        svc = SearchConsoleIntelligenceService(self.db)
        try:
            sync = await svc.run_sync(organization_id=organization_id, include_comparison=True)
        except HTTPException as exc:
            if exc.status_code in (400, 404):
                await self._create_alert(
                    organization_id=organization_id,
                    monitoring_run_id=monitoring_run_id,
                    alert_type=SeoMonitoringAlertType.authorization_required,
                    severity=SeoMonitoringAlertSeverity.medium,
                    title="Search Console authorization required",
                    summary=_safe_text(str(exc.detail)),
                    dedupe_key=f"gsc-auth:{organization_id}",
                    entity_type="integration",
                )
                return {"completed": False, "reason": "authorization_required", "records_evaluated": 0}
            raise

        alerts = 0
        changes = 0
        if sync.status == SearchConsoleSyncStatus.completed:
            changes, alerts = await self._evaluate_gsc_metrics(
                organization_id=organization_id,
                config=config,
                sync=sync,
                monitoring_run_id=monitoring_run_id,
            )
            await self._evaluate_gsc_opportunities(
                organization_id=organization_id,
                sync=sync,
                monitoring_run_id=monitoring_run_id,
            )
        return {
            "completed": sync.status == SearchConsoleSyncStatus.completed,
            "sync_id": str(sync.id),
            "changes_detected": changes,
            "alerts_generated": alerts,
            "records_evaluated": 1,
        }

    async def _evaluate_gsc_metrics(
        self,
        *,
        organization_id: UUID,
        config: SeoMonitoringConfig,
        sync: SearchConsoleSync,
        monitoring_run_id: UUID,
    ) -> tuple[int, int]:
        totals = (sync.meta or {}).get("totals") or {}
        compare_totals = (sync.meta or {}).get("compare_totals") or {}
        changes = 0
        alerts = 0

        clicks = float(totals.get("clicks", 0) or 0)
        prev_clicks = float(compare_totals.get("clicks", clicks) or clicks)
        if prev_clicks > 0:
            decline_pct = ((prev_clicks - clicks) / prev_clicks) * 100
            if decline_pct >= config.click_decline_threshold_pct:
                created = await self._create_alert(
                    organization_id=organization_id,
                    monitoring_run_id=monitoring_run_id,
                    alert_type=SeoMonitoringAlertType.gsc_clicks_decline,
                    severity=SeoMonitoringAlertSeverity.high,
                    title="Search Console clicks declined",
                    summary=f"Clicks declined {decline_pct:.1f}% ({prev_clicks:.0f} → {clicks:.0f})",
                    dedupe_key=f"gsc-clicks:{organization_id}",
                    threshold=f"{config.click_decline_threshold_pct}%",
                    observed_value=str(clicks),
                    previous_value=str(prev_clicks),
                    delta=f"-{decline_pct:.1f}%",
                    entity_type="search_console_sync",
                    entity_id=str(sync.id),
                )
                if created:
                    alerts += 1
                changes += 1
            elif decline_pct <= -config.click_decline_threshold_pct:
                await self._resolve_alerts_by_dedupe(organization_id, f"gsc-clicks:{organization_id}")

        impressions = float(totals.get("impressions", 0) or 0)
        prev_impressions = float(compare_totals.get("impressions", impressions) or impressions)
        if prev_impressions > 0:
            decline_pct = ((prev_impressions - impressions) / prev_impressions) * 100
            if decline_pct >= config.impression_decline_threshold_pct:
                created = await self._create_alert(
                    organization_id=organization_id,
                    monitoring_run_id=monitoring_run_id,
                    alert_type=SeoMonitoringAlertType.gsc_impressions_decline,
                    severity=SeoMonitoringAlertSeverity.medium,
                    title="Search Console impressions declined",
                    summary=f"Impressions declined {decline_pct:.1f}%",
                    dedupe_key=f"gsc-impressions:{organization_id}",
                    threshold=f"{config.impression_decline_threshold_pct}%",
                    observed_value=str(impressions),
                    previous_value=str(prev_impressions),
                    delta=f"-{decline_pct:.1f}%",
                    entity_type="search_console_sync",
                    entity_id=str(sync.id),
                )
                if created:
                    alerts += 1
                changes += 1

        position = float(totals.get("position", 0) or 0)
        prev_position = float(compare_totals.get("position", position) or position)
        if prev_position > 0 and abs(position - prev_position) >= config.position_change_threshold:
            created = await self._create_alert(
                organization_id=organization_id,
                monitoring_run_id=monitoring_run_id,
                alert_type=SeoMonitoringAlertType.gsc_position_change,
                severity=SeoMonitoringAlertSeverity.medium,
                title="Search Console average position changed",
                summary=f"Position changed {prev_position:.1f} → {position:.1f}",
                dedupe_key=f"gsc-position:{organization_id}",
                threshold=str(config.position_change_threshold),
                observed_value=str(position),
                previous_value=str(prev_position),
                delta=str(position - prev_position),
                entity_type="search_console_sync",
                entity_id=str(sync.id),
            )
            if created:
                alerts += 1
            changes += 1

        await self._upsert_snapshot(
            organization_id=organization_id,
            snapshot_type="search_console",
            snapshot_key="totals",
            metric_value={"clicks": clicks, "impressions": impressions, "position": position},
            source_run_id=str(sync.id),
        )
        return changes, alerts

    async def _evaluate_gsc_opportunities(
        self,
        *,
        organization_id: UUID,
        sync: SearchConsoleSync,
        monitoring_run_id: UUID,
    ) -> int:
        high_opps = (
            await self.db.execute(
                select(SearchConsoleOpportunity)
                .where(
                    SearchConsoleOpportunity.organization_id == organization_id,
                    SearchConsoleOpportunity.sync_id == sync.id,
                    SearchConsoleOpportunity.priority == SearchConsoleOpportunityPriority.high,
                )
                .limit(20)
            )
        ).scalars().all()
        alerts = 0
        for opp in high_opps:
            created = await self._create_alert(
                organization_id=organization_id,
                monitoring_run_id=monitoring_run_id,
                alert_type=SeoMonitoringAlertType.gsc_new_opportunity,
                severity=SeoMonitoringAlertSeverity.medium,
                title=_safe_text(f"Search Console opportunity: {opp.opportunity_type}", max_len=MAX_TITLE_LEN),
                summary=_safe_text(opp.explanation or opp.opportunity_type),
                dedupe_key=f"gsc-opp:{organization_id}:{opp.id}",
                entity_type="search_console_opportunity",
                entity_id=str(opp.id),
                affected_urls=[opp.page_url] if opp.page_url else [],
            )
            if created:
                alerts += 1
        return alerts

    async def _schedule_competitor_crawls(
        self,
        *,
        organization_id: UUID,
        config: SeoMonitoringConfig,
        monitoring_run_id: UUID,
    ) -> dict:
        competitors = (
            await self.db.execute(
                select(SeoCompetitor)
                .where(
                    SeoCompetitor.organization_id == organization_id,
                    SeoCompetitor.status == "active",
                )
                .limit(config.max_competitors_per_cycle)
            )
        ).scalars().all()
        scheduled = 0
        svc = SeoCompetitorService(self.db)
        for competitor in competitors:
            try:
                crawl = await svc.start_crawl(
                    organization_id=organization_id,
                    competitor_id=competitor.id,
                )
                job = await self.db.scalar(
                    select(BackgroundJob).where(BackgroundJob.id == crawl.job_id).limit(1)
                )
                if job:
                    job.payload = {**(job.payload or {}), "monitoring_run_id": str(monitoring_run_id)}
                scheduled += 1
            except HTTPException:
                continue
        await self.db.flush()
        return {"scheduled": scheduled}

    async def _evaluate_pending_actions(self, *, organization_id: UUID, run: SeoMonitoringRun) -> int:
        pending = await self.db.scalar(
            select(func.count())
            .select_from(AIAction)
            .where(
                AIAction.organization_id == organization_id,
                AIAction.status == AIActionStatus.pending,
            )
        )
        if not pending:
            await self._resolve_alerts_by_dedupe(organization_id, f"pending-actions:{organization_id}")
            return 0
        created = await self._create_alert(
            organization_id=organization_id,
            monitoring_run_id=run.id,
            alert_type=SeoMonitoringAlertType.action_pending_approval,
            severity=SeoMonitoringAlertSeverity.medium,
            title="SEO actions pending approval",
            summary=f"{pending} SEO action(s) awaiting review — no automatic execution.",
            dedupe_key=f"pending-actions:{organization_id}",
            observed_value=str(pending),
            entity_type="ai_action_queue",
        )
        return 1 if created else 0

    async def _create_alert(
        self,
        *,
        organization_id: UUID,
        alert_type: SeoMonitoringAlertType,
        severity: SeoMonitoringAlertSeverity,
        title: str,
        summary: str,
        dedupe_key: str,
        monitoring_run_id: UUID | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        affected_urls: list | None = None,
        evidence: dict | None = None,
        threshold: str | None = None,
        observed_value: str | None = None,
        previous_value: str | None = None,
        delta: str | None = None,
    ) -> bool:
        config = await self.get_or_create_config(organization_id=organization_id)
        existing = await self.db.scalar(
            select(SeoMonitoringAlert)
            .where(
                SeoMonitoringAlert.organization_id == organization_id,
                SeoMonitoringAlert.dedupe_key == dedupe_key,
            )
            .limit(1)
        )
        if existing:
            if existing.status == SeoMonitoringAlertStatus.resolved.value:
                existing.status = SeoMonitoringAlertStatus.open.value
                existing.resolved_at = None
                existing.title = _safe_text(title, max_len=MAX_TITLE_LEN)
                existing.summary = _safe_text(summary)
                existing.monitoring_run_id = monitoring_run_id
                await self.db.flush()
                return True
            if existing.status in (
                SeoMonitoringAlertStatus.open.value,
                SeoMonitoringAlertStatus.acknowledged.value,
            ):
                return False
            cooldown = timedelta(hours=max(config.alert_cooldown_hours, 1))
            if existing.created_at and datetime.now(timezone.utc) - existing.created_at.replace(tzinfo=timezone.utc) < cooldown:
                return False

        urls = (affected_urls or [])[:MAX_ALERT_URLS]
        alert = SeoMonitoringAlert(
            organization_id=organization_id,
            monitoring_run_id=monitoring_run_id,
            alert_type=alert_type.value,
            severity=severity.value,
            title=_safe_text(title, max_len=MAX_TITLE_LEN),
            summary=_safe_text(summary),
            entity_type=entity_type,
            entity_id=entity_id,
            affected_urls=urls,
            evidence=evidence or {},
            threshold=threshold,
            observed_value=observed_value,
            previous_value=previous_value,
            delta=delta,
            dedupe_key=dedupe_key,
            status=SeoMonitoringAlertStatus.open.value,
        )
        self.db.add(alert)
        await self.db.flush()
        return True

    async def _resolve_alerts_by_dedupe(self, organization_id: UUID, dedupe_key: str) -> None:
        rows = (
            await self.db.execute(
                select(SeoMonitoringAlert).where(
                    SeoMonitoringAlert.organization_id == organization_id,
                    SeoMonitoringAlert.dedupe_key == dedupe_key,
                    SeoMonitoringAlert.status.in_(
                        (SeoMonitoringAlertStatus.open.value, SeoMonitoringAlertStatus.acknowledged.value)
                    ),
                )
            )
        ).scalars().all()
        now = datetime.now(timezone.utc)
        for row in rows:
            row.status = SeoMonitoringAlertStatus.resolved.value
            row.resolved_at = now
        await self.db.flush()

    async def _resolve_alerts_by_prefix(self, organization_id: UUID, dedupe_prefix: str) -> None:
        rows = (
            await self.db.execute(
                select(SeoMonitoringAlert).where(
                    SeoMonitoringAlert.organization_id == organization_id,
                    SeoMonitoringAlert.dedupe_key == dedupe_prefix,
                    SeoMonitoringAlert.status.in_(
                        (SeoMonitoringAlertStatus.open.value, SeoMonitoringAlertStatus.acknowledged.value)
                    ),
                )
            )
        ).scalars().all()
        now = datetime.now(timezone.utc)
        for row in rows:
            row.status = SeoMonitoringAlertStatus.resolved.value
            row.resolved_at = now
        await self.db.flush()

    async def _get_snapshot(
        self, organization_id: UUID, snapshot_type: str, snapshot_key: str
    ) -> SeoMonitoringSnapshot | None:
        return await self.db.scalar(
            select(SeoMonitoringSnapshot).where(
                SeoMonitoringSnapshot.organization_id == organization_id,
                SeoMonitoringSnapshot.snapshot_type == snapshot_type,
                SeoMonitoringSnapshot.snapshot_key == snapshot_key,
            ).limit(1)
        )

    async def _upsert_snapshot(
        self,
        *,
        organization_id: UUID,
        snapshot_type: str,
        snapshot_key: str,
        metric_value: dict,
        source_run_id: str | None,
    ) -> None:
        row = await self._get_snapshot(organization_id, snapshot_type, snapshot_key)
        if row:
            row.metric_value = metric_value
            row.source_run_id = source_run_id
        else:
            self.db.add(
                SeoMonitoringSnapshot(
                    organization_id=organization_id,
                    snapshot_type=snapshot_type,
                    snapshot_key=snapshot_key,
                    metric_value=metric_value,
                    source_run_id=source_run_id,
                )
            )
        await self.db.flush()
