"""SEO weekly report generation service (M9.16)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.queue import JobQueue
from app.jobs.registry import SEO_REPORT_GENERATE
from app.models.automation import BackgroundJob
from app.models.enums import JobStatus
from app.models.seo_monitoring import SeoMonitoringAlert, SeoMonitoringRun, SeoMonitoringSnapshot
from app.models.seo_weekly_report import SeoReportConfig, SeoWeeklyReport
from app.services.seo_dashboard_service import SeoDashboardService

REPORT_VERSION = "v1"


def report_generation_key(
    organization_id: UUID,
    period_start: date,
    period_end: date,
    report_version: str = REPORT_VERSION,
) -> str:
    return f"{organization_id}:{period_start.isoformat()}:{period_end.isoformat()}:{report_version}"


DISCLAIMER = (
    "Weekly SEO reports aggregate persisted GrowthOS data only. "
    "They do not execute SEO actions, publish content, or modify websites."
)
MAX_LIST = 50
MAX_SECTION_ITEMS = 20
MAX_SUMMARY_LEN = 4000
_UNSAFE = re.compile(r"[<>&\"']")


def _safe_text(value: str | None, *, max_len: int = 2000) -> str:
    if not value:
        return ""
    return _UNSAFE.sub("", str(value))[:max_len]


def weekly_period_for_date(*, anchor: date | None = None) -> tuple[date, date]:
    """Return UTC Monday–Sunday week containing anchor (inclusive end Sunday)."""
    anchor = anchor or datetime.now(timezone.utc).date()
    monday = anchor - timedelta(days=anchor.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def previous_weekly_period(period_start: date, period_end: date) -> tuple[date, date]:
    prev_end = period_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=(period_end - period_start).days)
    return prev_start, prev_end


class SeoWeeklyReportService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_or_create_config(self, *, organization_id: UUID) -> SeoReportConfig:
        row = await self.db.scalar(
            select(SeoReportConfig).where(SeoReportConfig.organization_id == organization_id).limit(1)
        )
        if row:
            return row
        row = SeoReportConfig(organization_id=organization_id)
        self.db.add(row)
        await self.db.flush()
        return row

    async def update_config(self, *, organization_id: UUID, reporting_enabled: bool) -> SeoReportConfig:
        config = await self.get_or_create_config(organization_id=organization_id)
        config.reporting_enabled = reporting_enabled
        await self.db.flush()
        return config

    async def list_reports(
        self,
        *,
        organization_id: UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[SeoWeeklyReport], int]:
        limit = min(limit, MAX_LIST)
        total = await self.db.scalar(
            select(func.count()).select_from(SeoWeeklyReport).where(SeoWeeklyReport.organization_id == organization_id)
        )
        rows = (
            await self.db.execute(
                select(SeoWeeklyReport)
                .where(SeoWeeklyReport.organization_id == organization_id)
                .order_by(SeoWeeklyReport.period_start.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        return list(rows), int(total or 0)

    async def get_report(self, *, organization_id: UUID, report_id: UUID) -> SeoWeeklyReport:
        row = await self.db.scalar(
            select(SeoWeeklyReport)
            .where(SeoWeeklyReport.id == report_id, SeoWeeklyReport.organization_id == organization_id)
            .limit(1)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
        return row

    async def get_existing_report(
        self,
        *,
        organization_id: UUID,
        period_start: date,
        period_end: date,
    ) -> SeoWeeklyReport | None:
        return await self.db.scalar(
            select(SeoWeeklyReport).where(
                SeoWeeklyReport.organization_id == organization_id,
                SeoWeeklyReport.period_start == period_start,
                SeoWeeklyReport.period_end == period_end,
                SeoWeeklyReport.report_version == REPORT_VERSION,
                SeoWeeklyReport.status == "completed",
            ).limit(1)
        )

    async def enqueue_generate(
        self,
        *,
        organization_id: UUID,
        period_start: date | None = None,
        period_end: date | None = None,
    ) -> SeoWeeklyReport:
        if period_start and period_end:
            if period_end < period_start:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="period_end must be >= period_start")
        else:
            period_start, period_end = weekly_period_for_date()

        existing = await self.get_existing_report(
            organization_id=organization_id,
            period_start=period_start,
            period_end=period_end,
        )
        if existing:
            return existing

        gen_key = report_generation_key(organization_id, period_start, period_end, REPORT_VERSION)
        inflight = await self.db.scalar(
            select(SeoWeeklyReport).where(
                SeoWeeklyReport.organization_id == organization_id,
                SeoWeeklyReport.generation_key == gen_key,
                SeoWeeklyReport.status.in_(("queued", "running")),
            ).limit(1)
        )
        if inflight:
            return inflight

        report = SeoWeeklyReport(
            organization_id=organization_id,
            period_start=period_start,
            period_end=period_end,
            status="queued",
            report_version=REPORT_VERSION,
            generation_key=gen_key,
            data_freshness={},
            summary="",
            report_payload={},
            limitations=[],
        )
        self.db.add(report)
        await self.db.flush()

        job = await JobQueue(self.db).enqueue(
            job_type=SEO_REPORT_GENERATE,
            payload={
                "report_id": str(report.id),
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
            },
            organization_id=organization_id,
            dedupe_key=f"seo-report:{gen_key}",
            max_attempts=3,
        )
        report.background_job_id = job.id
        await self.db.flush()
        return report

    async def generate_report(self, *, organization_id: UUID, report_id: UUID) -> SeoWeeklyReport:
        report = await self.get_report(organization_id=organization_id, report_id=report_id)
        if report.status == "completed":
            return report

        report.status = "running"
        report.error_message = None
        started = datetime.now(timezone.utc)
        await self.db.flush()

        try:
            payload, summary, limitations, freshness = await self._build_report_payload(
                organization_id=organization_id,
                period_start=report.period_start,
                period_end=report.period_end,
            )
            report.report_payload = payload
            report.summary = _safe_text(summary, max_len=MAX_SUMMARY_LEN)
            report.limitations = limitations[:MAX_SECTION_ITEMS]
            report.data_freshness = freshness
            report.status = "completed"
            report.generated_at = datetime.now(timezone.utc)
            config = await self.get_or_create_config(organization_id=organization_id)
            config.last_report_at = report.generated_at
            config.last_failure_reason = None
            await self.db.flush()
            return report
        except Exception as exc:
            safe = _safe_text(str(exc), max_len=512)
            report.status = "failed"
            report.error_message = safe
            config = await self.get_or_create_config(organization_id=organization_id)
            config.last_failure_reason = safe
            await self.db.flush()
            raise

    async def _build_report_payload(
        self,
        *,
        organization_id: UUID,
        period_start: date,
        period_end: date,
    ) -> tuple[dict, str, list[str], dict]:
        dash = await SeoDashboardService(self.db).get_dashboard(organization_id=organization_id)
        dash_dict = dash.model_dump(mode="json")

        prev_start, prev_end = previous_weekly_period(period_start, period_end)
        monitoring = await self._monitoring_section(organization_id, period_start, period_end)
        trends = await self._trends_section(organization_id)
        limitations = self._build_limitations(dash_dict, monitoring)

        sections = {
            "executive_summary": self._executive_summary(dash_dict, monitoring, period_start, period_end),
            "technical_seo": self._section_from_dashboard(dash_dict, "technical", label="GrowthOS technical findings"),
            "search_console": self._section_from_dashboard(
                dash_dict,
                "search_console",
                label="Search Console observed metrics",
                unavailable_msg="Search Console data unavailable for this reporting period.",
            ),
            "keywords": self._section_from_dashboard(dash_dict, "keywords", label="GrowthOS keyword opportunities"),
            "topics": self._section_from_dashboard(
                dash_dict,
                "topics",
                label="GrowthOS topic clusters (lexical/Jaccard-based)",
            ),
            "competitors_content_gaps": self._section_from_dashboard(
                dash_dict,
                "competitors",
                label="Competitor intelligence from configured crawls only",
            ),
            "content_pipeline": self._section_from_dashboard(dash_dict, "content", label="Content briefs and drafts"),
            "on_page": self._section_from_dashboard(dash_dict, "on_page", label="On-page optimization findings"),
            "schema": self._section_from_dashboard(
                dash_dict,
                "schema_panel",
                label="Schema artifacts and validation (planning only — not deployed)",
            ),
            "internal_links": self._section_from_dashboard(dash_dict, "internal_links", label="Internal-link opportunities"),
            "recommendations": {
                "source": "growthos_recommendations",
                "count": dash_dict.get("overview", {}).get("recommendations", 0),
                "available": dash_dict.get("overview", {}).get("recommendations", 0) > 0,
                "note": "See /seo/recommendations for full recommendation detail.",
            },
            "seo_actions": self._section_from_dashboard(
                dash_dict,
                "actions",
                label="SEO actions (review-only — no autonomous execution)",
            ),
            "monitoring_alerts": monitoring,
            "trends": trends,
            "attention": {
                "source": "growthos_attention",
                "items": (dash_dict.get("attention") or {}).get("items", [])[:MAX_SECTION_ITEMS],
            },
            "data_limitations": limitations,
        }

        summary = self._render_summary(sections, period_start, period_end)
        freshness = {
            "dashboard_generated_at": dash_dict.get("generated_at"),
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "previous_period_start": prev_start.isoformat(),
            "previous_period_end": prev_end.isoformat(),
            "timezone_note": "Reporting periods use UTC week boundaries (Monday–Sunday). Organization timezone not configured.",
        }
        payload = {
            "report_version": REPORT_VERSION,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "disclaimer": DISCLAIMER,
            "sections": sections,
            "overview": dash_dict.get("overview", {}),
        }
        return payload, summary, limitations, freshness

    def _section_from_dashboard(
        self,
        dash: dict,
        key: str,
        *,
        label: str,
        unavailable_msg: str | None = None,
    ) -> dict:
        section = dash.get(key) or {}
        available = bool(section.get("available"))
        return {
            "source_label": label,
            "available": available,
            "empty_message": section.get("empty_message") if not available else None,
            "unavailable_message": unavailable_msg if not available and unavailable_msg else None,
            "data": section,
        }

    async def _monitoring_section(
        self,
        organization_id: UUID,
        period_start: date,
        period_end: date,
    ) -> dict:
        period_start_dt = datetime.combine(period_start, datetime.min.time(), tzinfo=timezone.utc)
        period_end_dt = datetime.combine(period_end, datetime.max.time(), tzinfo=timezone.utc)

        runs = (
            await self.db.execute(
                select(SeoMonitoringRun)
                .where(
                    SeoMonitoringRun.organization_id == organization_id,
                    SeoMonitoringRun.created_at >= period_start_dt,
                    SeoMonitoringRun.created_at <= period_end_dt,
                )
                .order_by(SeoMonitoringRun.created_at.desc())
                .limit(MAX_SECTION_ITEMS)
            )
        ).scalars().all()

        alerts = (
            await self.db.execute(
                select(SeoMonitoringAlert)
                .where(
                    SeoMonitoringAlert.organization_id == organization_id,
                    SeoMonitoringAlert.created_at >= period_start_dt,
                    SeoMonitoringAlert.created_at <= period_end_dt,
                )
                .order_by(SeoMonitoringAlert.created_at.desc())
                .limit(MAX_SECTION_ITEMS)
            )
        ).scalars().all()

        open_alerts = await self.db.scalar(
            select(func.count())
            .select_from(SeoMonitoringAlert)
            .where(
                SeoMonitoringAlert.organization_id == organization_id,
                SeoMonitoringAlert.status.in_(("open", "acknowledged")),
            )
        )

        return {
            "source_label": "M9.15 continuous monitoring",
            "available": bool(runs or alerts),
            "runs_in_period": len(runs),
            "alerts_in_period": len(alerts),
            "open_alerts_total": int(open_alerts or 0),
            "runs": [
                {
                    "id": str(r.id),
                    "status": r.status,
                    "alerts_generated": r.alerts_generated,
                    "changes_detected": r.changes_detected,
                    "failure_reason": r.failure_reason,
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                }
                for r in runs[:10]
            ],
            "alerts": [
                {
                    "id": str(a.id),
                    "alert_type": a.alert_type,
                    "severity": a.severity,
                    "title": _safe_text(a.title, max_len=256),
                    "status": a.status,
                }
                for a in alerts[:10]
            ],
        }

    async def _trends_section(self, organization_id: UUID) -> dict:
        snapshots = (
            await self.db.execute(
                select(SeoMonitoringSnapshot)
                .where(SeoMonitoringSnapshot.organization_id == organization_id)
                .limit(MAX_SECTION_ITEMS)
            )
        ).scalars().all()
        if not snapshots:
            return {
                "available": False,
                "note": "Previous-period comparison data unavailable — insufficient monitoring snapshots.",
            }
        items = []
        for snap in snapshots:
            items.append(
                {
                    "snapshot_type": snap.snapshot_type,
                    "snapshot_key": snap.snapshot_key,
                    "metric_value": snap.metric_value,
                    "source_run_id": snap.source_run_id,
                    "updated_at": snap.updated_at.isoformat() if snap.updated_at else None,
                }
            )
        return {"available": True, "snapshots": items}

    def _executive_summary(
        self,
        dash: dict,
        monitoring: dict,
        period_start: date,
        period_end: date,
    ) -> dict:
        overview = dash.get("overview") or {}
        return {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "highlights": [
                f"Technical findings: {overview.get('technical_findings', 0)} ({overview.get('critical_high_findings', 0)} high/critical)",
                f"Keyword opportunities: {overview.get('keyword_opportunities', 0)}",
                f"Pending SEO actions: {overview.get('pending_actions', 0)} (review-only)",
                f"Open monitoring alerts: {monitoring.get('open_alerts_total', 0)}",
                f"Content gaps: {overview.get('content_gaps', 0)}",
            ],
            "no_synthetic_seo_score": True,
        }

    def _render_summary(self, sections: dict, period_start: date, period_end: date) -> str:
        exec_sum = sections.get("executive_summary") or {}
        highlights = exec_sum.get("highlights") or []
        lines = [
            f"Weekly SEO report for {period_start.isoformat()} to {period_end.isoformat()} (UTC).",
            "This report aggregates persisted GrowthOS data only.",
        ]
        lines.extend(highlights[:8])
        return "\n".join(lines)

    def _build_limitations(self, dash: dict, monitoring: dict) -> list[str]:
        limits = [
            "Search Console data may have reporting latency and is not real-time.",
            "Competitor intelligence is limited to configured crawl data — no competitor traffic or rankings.",
            "No synthetic SEO score is produced.",
            "SEO actions remain review-only — this report does not execute changes.",
            "Keyword data reflects GrowthOS opportunities, not search volume.",
            "Schema section reflects planning/validation artifacts — schema is not deployed by GrowthOS.",
            "Reporting periods use UTC week boundaries; organization timezone is not configured.",
        ]
        sc = dash.get("search_console") or {}
        if not sc.get("connected"):
            limits.append("Search Console is not connected for this organization.")
        if not sc.get("available"):
            limits.append("Search Console metrics unavailable for this period.")
        if not monitoring.get("available"):
            limits.append("Monitoring data unavailable — enable M9.15 monitoring for change/alert sections.")
        tech = dash.get("technical") or {}
        if not tech.get("available"):
            limits.append("No completed crawl — technical SEO section limited.")
        return limits
