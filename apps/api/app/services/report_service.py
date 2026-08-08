from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.orchestrator import get_orchestrator
from app.core.config import get_settings
from app.models.ai_ops import Report
from app.models.marketing import Campaign, SocialPost
from app.models.organization import Organization
from app.schemas.report import ReportOut
from app.security.audit import write_audit
from app.services.analytics_service import AnalyticsService
from app.services.client_service import ClientService


def _wrap(text: str, width: int) -> list[str]:  # noqa: A001 — builtin list in annotations is fine with future import
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if len(trial) <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


class ReportService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.analytics = AnalyticsService(db)
        self.clients = ClientService(db)
        self.orchestrator = get_orchestrator()

    async def list(self, organization_id: UUID, client_id: UUID) -> list[ReportOut]:
        await self.clients.get_client(organization_id, client_id)
        rows = list(
            (
                await self.db.execute(
                    select(Report)
                    .where(Report.organization_id == organization_id, Report.client_id == client_id)
                    .order_by(Report.created_at.desc())
                )
            ).scalars().all()
        )
        return [ReportOut.model_validate(r) for r in rows]

    async def get(self, organization_id: UUID, client_id: UUID, report_id: UUID) -> ReportOut:
        row = await self.db.scalar(
            select(Report).where(
                Report.id == report_id,
                Report.organization_id == organization_id,
                Report.client_id == client_id,
            )
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
        return ReportOut.model_validate(row)

    async def generate(
        self,
        organization: Organization,
        user_id: UUID,
        client_id: UUID,
        *,
        period_days: int,
    ) -> ReportOut:
        context = await self.clients.build_client_context(organization, client_id)
        analytics = await self.analytics.get_analytics(
            organization.id,
            client_id=client_id,
            period_days=period_days,
            demo_mode=organization.demo_mode,
        )

        posts = list(
            (
                await self.db.execute(
                    select(SocialPost)
                    .where(SocialPost.organization_id == organization.id, SocialPost.client_id == client_id)
                    .limit(20)
                )
            ).scalars().all()
        )
        camps = list(
            (
                await self.db.execute(
                    select(Campaign).where(
                        Campaign.organization_id == organization.id,
                        Campaign.client_id == client_id,
                    )
                )
            ).scalars().all()
        )

        def post_score(p: SocialPost) -> float:
            m = p.metrics or {}
            return float(m.get("engagement") or m.get("impressions") or 0)

        ranked_posts = sorted(posts, key=post_score, reverse=True)
        top_content = [
            {
                "hook": p.hook,
                "platform": p.platform,
                "metrics": p.metrics or {},
                "note": None if p.metrics else "Insufficient data.",
            }
            for p in ranked_posts[:3]
        ]
        worst_content = [
            {
                "hook": p.hook,
                "platform": p.platform,
                "metrics": p.metrics or {},
                "note": None if p.metrics else "Insufficient data.",
            }
            for p in (ranked_posts[-3:] if ranked_posts else [])
        ]

        def camp_cpl(c: Campaign) -> float:
            m = c.metrics or {}
            if m.get("cpl") is not None:
                return float(m["cpl"])
            return float(c.spend or 0)

        ranked_camps = sorted(camps, key=camp_cpl)
        top_campaigns = [
            {"name": c.name, "platform": c.platform, "spend": float(c.spend), "metrics": c.metrics or {}}
            for c in ranked_camps[:3]
        ]
        worst_campaigns = [
            {"name": c.name, "platform": c.platform, "spend": float(c.spend), "metrics": c.metrics or {}}
            for c in (ranked_camps[-3:] if ranked_camps else [])
        ]

        draft = await self.orchestrator.weekly_report(context, period_label=f"Last {period_days} days")

        content = {
            "executive_summary": draft.executive_summary,
            "key_metrics": {
                "spend": float(analytics.current.spend),
                "leads": analytics.current.leads,
                "revenue": float(analytics.current.revenue),
                "cpl": float(analytics.current.cpl) if analytics.current.cpl is not None else None,
                "ctr": float(analytics.current.ctr) if analytics.current.ctr is not None else None,
                "conversion_rate": float(analytics.current.conversion_rate)
                if analytics.current.conversion_rate is not None
                else None,
            },
            "growth": draft.growth,
            "declines": draft.declines,
            "deltas": analytics.deltas,
            "top_content": top_content,
            "worst_performing_content": worst_content,
            "top_campaigns": top_campaigns,
            "worst_campaigns": worst_campaigns,
            "lead_performance": analytics.sections.get("leads", {}),
            "ai_insights": draft.insights,
            "next_week_strategy": draft.next_week_strategy,
            "insufficient_data": list(set(draft.insufficient_data + analytics.insufficient_data)),
            "data_source": analytics.data_source,
        }

        end = date.today()
        start = end - timedelta(days=period_days - 1)
        report = Report(
            organization_id=organization.id,
            client_id=client_id,
            title=f"Weekly Report · {context.business_name}",
            period_start=start,
            period_end=end,
            content=content,
            status="ready",
        )
        self.db.add(report)
        await self.db.flush()
        report.export_path = self._write_pdf(report)
        await write_audit(
            self.db,
            action="report.generate",
            organization_id=organization.id,
            user_id=user_id,
            resource_type="report",
            resource_id=str(report.id),
        )
        await self.db.flush()
        await self.db.refresh(report)
        out = ReportOut.model_validate(report)
        out.data_source = analytics.data_source
        return out

    def _write_pdf(self, report: Report) -> str:
        settings = get_settings()
        out_dir = Path(settings.storage_local_path) / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{report.id}.pdf"
        body = report.content or {}

        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.pdfgen import canvas
        except ImportError:
            text_path = out_dir / f"{report.id}.txt"
            text_path.write_text(
                f"{report.title}\n{report.period_start} to {report.period_end}\n\n"
                f"Executive summary:\n{body.get('executive_summary', '')}\n\n"
                f"Next week:\n{body.get('next_week_strategy', '')}\n",
                encoding="utf-8",
            )
            return str(text_path)

        c = canvas.Canvas(str(path), pagesize=letter)
        width, height = letter
        y = height - 50
        c.setFont("Helvetica-Bold", 14)
        c.drawString(40, y, report.title[:90])
        y -= 20
        c.setFont("Helvetica", 10)
        c.drawString(40, y, f"Period: {report.period_start} → {report.period_end}")
        y -= 30
        sections = [
            ("Executive summary", body.get("executive_summary")),
            ("Next week strategy", body.get("next_week_strategy")),
            ("Key metrics", str(body.get("key_metrics"))),
            ("AI insights", "; ".join(body.get("ai_insights") or [])),
            ("Insufficient data", "; ".join(body.get("insufficient_data") or []) or "None"),
        ]
        for title, value in sections:
            if y < 80:
                c.showPage()
                y = height - 50
            c.setFont("Helvetica-Bold", 11)
            c.drawString(40, y, title)
            y -= 16
            c.setFont("Helvetica", 10)
            for line in _wrap(str(value or "Insufficient data."), 95):
                if y < 50:
                    c.showPage()
                    y = height - 50
                c.drawString(40, y, line)
                y -= 13
            y -= 10
        c.save()
        return str(path)

    def resolve_export_path(self, organization_id: UUID, client_id: UUID, report_id: UUID) -> Path:
        # Path validated after DB ownership check in the route.
        settings = get_settings()
        return Path(settings.storage_local_path) / "reports" / f"{report_id}.pdf"
