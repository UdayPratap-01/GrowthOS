"""Technical SEO analysis — tenant-scoped findings persistence and retrieval."""

from __future__ import annotations

from collections import Counter
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import SeoFindingSeverity, SeoFindingStatus
from app.models.seo import SeoCrawl, SeoCrawlPage, SeoFinding
from app.seo.analysis import DISCLAIMER, analyze_crawl


class SeoAnalysisService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def run_analysis(self, *, organization_id: UUID, crawl_id: UUID) -> dict:
        crawl = await self._load_crawl(organization_id, crawl_id)
        pages = (
            await self.db.execute(
                select(SeoCrawlPage)
                .where(SeoCrawlPage.crawl_id == crawl_id, SeoCrawlPage.organization_id == organization_id)
                .order_by(SeoCrawlPage.depth, SeoCrawlPage.created_at)
            )
        ).scalars().all()

        drafts = analyze_crawl(crawl, list(pages))
        await self.db.execute(delete(SeoFinding).where(SeoFinding.crawl_id == crawl_id))
        for draft in drafts:
            self.db.add(
                SeoFinding(
                    crawl_id=crawl_id,
                    organization_id=organization_id,
                    page_id=draft.page_id,
                    rule_id=draft.rule_id,
                    category=draft.category,
                    severity=SeoFindingSeverity(draft.severity),
                    status=SeoFindingStatus.open,
                    dedupe_key=draft.dedupe_key()[:512],
                    title=draft.title[:255],
                    description=draft.description,
                    evidence={**draft.evidence, "data_provenance": "technical_seo_analysis"},
                    observed_value=draft.observed_value,
                    expected_or_heuristic=draft.expected_or_heuristic,
                    recommendation=draft.recommendation,
                    url=draft.url,
                )
            )

        crawl.stats = {
            **(crawl.stats or {}),
            "analysis_status": "completed",
            "findings_count": len(drafts),
        }
        await self.db.flush()
        return {"crawl_id": str(crawl_id), "findings_count": len(drafts), "analysis_status": "completed"}

    async def ensure_analysis(self, *, organization_id: UUID, crawl_id: UUID) -> None:
        crawl = await self._load_crawl(organization_id, crawl_id)
        if (crawl.stats or {}).get("analysis_status") == "completed":
            existing = await self.db.scalar(
                select(func.count()).select_from(SeoFinding).where(SeoFinding.crawl_id == crawl_id)
            )
            if (existing or 0) > 0:
                return
        await self.run_analysis(organization_id=organization_id, crawl_id=crawl_id)

    async def list_findings(
        self,
        *,
        organization_id: UUID,
        crawl_id: UUID,
        category: str | None = None,
        severity: str | None = None,
        status_filter: str | None = None,
        rule_id: str | None = None,
        url: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SeoFinding]:
        await self.ensure_analysis(organization_id=organization_id, crawl_id=crawl_id)
        q = select(SeoFinding).where(
            SeoFinding.crawl_id == crawl_id,
            SeoFinding.organization_id == organization_id,
        )
        if category:
            q = q.where(SeoFinding.category == category)
        if severity:
            q = q.where(SeoFinding.severity == severity.upper())
        if status_filter:
            q = q.where(SeoFinding.status == status_filter)
        if rule_id:
            q = q.where(SeoFinding.rule_id == rule_id)
        if url:
            q = q.where(SeoFinding.url == url)
        q = q.order_by(SeoFinding.category, SeoFinding.rule_id, SeoFinding.url, SeoFinding.created_at).offset(offset).limit(min(limit, 500))
        return list((await self.db.execute(q)).scalars().all())

    async def get_finding(self, *, organization_id: UUID, crawl_id: UUID, finding_id: UUID) -> SeoFinding:
        await self.ensure_analysis(organization_id=organization_id, crawl_id=crawl_id)
        row = await self.db.scalar(
            select(SeoFinding).where(
                SeoFinding.id == finding_id,
                SeoFinding.crawl_id == crawl_id,
                SeoFinding.organization_id == organization_id,
            ).limit(1)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finding not found")
        return row

    async def summary(self, *, organization_id: UUID, crawl_id: UUID) -> dict:
        await self.ensure_analysis(organization_id=organization_id, crawl_id=crawl_id)
        crawl = await self._load_crawl(organization_id, crawl_id)
        rows = (
            await self.db.execute(
                select(SeoFinding).where(
                    SeoFinding.crawl_id == crawl_id,
                    SeoFinding.organization_id == organization_id,
                )
            )
        ).scalars().all()
        by_severity = Counter(str(r.severity.value if hasattr(r.severity, "value") else r.severity) for r in rows)
        by_category = Counter(r.category for r in rows)
        affected_pages = len({r.page_id for r in rows if r.page_id})
        return {
            "crawl_id": crawl_id,
            "total_findings": len(rows),
            "by_severity": dict(by_severity),
            "by_category": dict(by_category),
            "affected_pages": affected_pages,
            "analysis_status": (crawl.stats or {}).get("analysis_status", "pending"),
            "disclaimer": DISCLAIMER,
        }

    async def compare(self, *, organization_id: UUID, crawl_id: UUID, other_crawl_id: UUID) -> dict:
        await self.ensure_analysis(organization_id=organization_id, crawl_id=crawl_id)
        await self.ensure_analysis(organization_id=organization_id, crawl_id=other_crawl_id)
        await self._load_crawl(organization_id, other_crawl_id)

        def key(row: SeoFinding) -> str:
            return f"{row.rule_id}|{row.url or ''}|{row.dedupe_key}"

        base_rows = (
            await self.db.execute(
                select(SeoFinding).where(SeoFinding.crawl_id == crawl_id, SeoFinding.organization_id == organization_id)
            )
        ).scalars().all()
        other_rows = (
            await self.db.execute(
                select(SeoFinding).where(SeoFinding.crawl_id == other_crawl_id, SeoFinding.organization_id == organization_id)
            )
        ).scalars().all()
        base_map = {key(r): r for r in base_rows}
        other_map = {key(r): r for r in other_rows}
        base_keys = set(base_map)
        other_keys = set(other_map)
        new_keys = other_keys - base_keys
        resolved_keys = base_keys - other_keys
        persistent_keys = base_keys & other_keys

        def brief(row: SeoFinding) -> dict:
            return {"rule_id": row.rule_id, "url": row.url, "severity": str(row.severity.value if hasattr(row.severity, "value") else row.severity), "title": row.title}

        return {
            "base_crawl_id": crawl_id,
            "compare_crawl_id": other_crawl_id,
            "new_findings": len(new_keys),
            "resolved_findings": len(resolved_keys),
            "persistent_findings": len(persistent_keys),
            "new": [brief(other_map[k]) for k in sorted(new_keys)[:50]],
            "resolved": [brief(base_map[k]) for k in sorted(resolved_keys)[:50]],
            "disclaimer": "Comparison reflects observable finding differences between crawls — not causation.",
        }

    async def _load_crawl(self, organization_id: UUID, crawl_id: UUID) -> SeoCrawl:
        row = await self.db.scalar(
            select(SeoCrawl).where(SeoCrawl.id == crawl_id, SeoCrawl.organization_id == organization_id).limit(1)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl not found")
        return row
