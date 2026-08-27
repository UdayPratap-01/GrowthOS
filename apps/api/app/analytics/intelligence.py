"""Performance intelligence orchestration — analysis only, never executes."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.confidence import score_confidence
from app.analytics.explain import deterministic_explanation, explain_recommendation
from app.analytics.signals import detect_signals
from app.analytics.windows import (
    AnalysisWindow,
    EntityPeriodComparison,
    load_entity_comparisons,
)
from app.core.config import get_settings
from app.jobs.queue import JobQueue
from app.models.client import Client
from app.models.enums import PerformanceRecommendationStatus
from app.models.performance_intelligence import PerformanceRecommendation
from app.security.audit import write_audit

logger = logging.getLogger(__name__)

ANALYTICS_ANALYZE_JOB = "analytics.analyze"


def analytics_analyze_dedupe_key(
    *,
    organization_id: UUID,
    client_id: UUID | None,
    window_days: int,
    platform: str | None,
    as_of: str | None = None,
) -> str:
    from datetime import date

    day = as_of or date.today().isoformat()
    client_part = str(client_id) if client_id else "org"
    plat = (platform or "all").lower()
    return f"analytics-analyze:{organization_id}:{client_part}:{plat}:w{window_days}:{day}"


def recommendation_fingerprint(
    *,
    organization_id: UUID,
    platform: str,
    entity_level: str,
    external_account_id: str,
    external_campaign_id: str,
    external_ad_set_id: str,
    external_ad_id: str,
    recommendation_type: str,
    metric: str,
    window_days: int,
    window_current_end: str,
) -> str:
    raw = "|".join(
        [
            str(organization_id),
            platform,
            entity_level,
            external_account_id,
            external_campaign_id,
            external_ad_set_id,
            external_ad_id,
            recommendation_type,
            metric,
            str(window_days),
            window_current_end,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


def validate_performance_intelligence_settings(settings=None) -> list[str]:
    settings = settings or get_settings()
    errors: list[str] = []
    if settings.performance_min_spend < 0:
        errors.append("PERFORMANCE_MIN_SPEND must be >= 0")
    if settings.performance_min_impressions < 0:
        errors.append("PERFORMANCE_MIN_IMPRESSIONS must be >= 0")
    if settings.performance_min_clicks < 0:
        errors.append("PERFORMANCE_MIN_CLICKS must be >= 0")
    if settings.performance_min_conversions < 0:
        errors.append("PERFORMANCE_MIN_CONVERSIONS must be >= 0")
    if not (1 <= settings.performance_significant_change_percent <= 500):
        errors.append("PERFORMANCE_SIGNIFICANT_CHANGE_PERCENT must be between 1 and 500")
    if not (5 <= settings.performance_sudden_change_percent <= 1000):
        errors.append("PERFORMANCE_SUDDEN_CHANGE_PERCENT must be between 5 and 1000")
    if settings.performance_recommendation_ttl_days < 1:
        errors.append("PERFORMANCE_RECOMMENDATION_TTL_DAYS must be >= 1")
    if settings.performance_min_days_with_data < 1:
        errors.append("PERFORMANCE_MIN_DAYS_WITH_DATA must be >= 1")
    return errors


class PerformanceIntelligenceService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def enqueue(
        self,
        *,
        organization_id: UUID,
        client_id: UUID | None,
        window_days: int = 7,
        platform: str | None = None,
        entity_level: str = "campaign",
        actor_user_id: UUID | None = None,
        use_ai_explanation: bool = True,
    ):
        if client_id is not None:
            client = await self.db.get(Client, client_id)
            if client is None or client.organization_id != organization_id:
                raise ValueError("client_id does not belong to this organization")
        if window_days not in (7, 14, 30):
            raise ValueError("window_days must be 7, 14, or 30")

        job = await JobQueue(self.db).enqueue(
            job_type=ANALYTICS_ANALYZE_JOB,
            payload={
                "client_id": str(client_id) if client_id else None,
                "window_days": window_days,
                "platform": platform,
                "entity_level": entity_level,
                "actor_user_id": str(actor_user_id) if actor_user_id else None,
                "use_ai_explanation": use_ai_explanation,
            },
            organization_id=organization_id,
            dedupe_key=analytics_analyze_dedupe_key(
                organization_id=organization_id,
                client_id=client_id,
                window_days=window_days,
                platform=platform,
            ),
            max_attempts=3,
        )
        await write_audit(
            self.db,
            action="analytics.analysis.enqueued",
            organization_id=organization_id,
            user_id=actor_user_id,
            resource_type="background_job",
            resource_id=str(job.id),
            details={
                "window_days": window_days,
                "platform": platform,
                "client_id": str(client_id) if client_id else None,
            },
        )
        return job

    async def analyze(
        self,
        *,
        organization_id: UUID,
        client_id: UUID | None = None,
        window_days: int = 7,
        platform: str | None = None,
        entity_level: str = "campaign",
        actor_user_id: UUID | None = None,
        use_ai_explanation: bool = True,
        trigger: str = "job",
    ) -> dict:
        settings = get_settings()
        if client_id is not None:
            client = await self.db.get(Client, client_id)
            if client is None or client.organization_id != organization_id:
                raise ValueError("client_id does not belong to this organization")
        if window_days not in (7, 14, 30):
            raise ValueError("window_days must be 7, 14, or 30")

        window = AnalysisWindow.for_days(window_days)
        await write_audit(
            self.db,
            action="analytics.analysis.started",
            organization_id=organization_id,
            user_id=actor_user_id,
            resource_type="analytics_analysis",
            resource_id=str(window_days),
            details={
                "trigger": trigger,
                "window_days": window_days,
                "platform": platform,
                "client_id": str(client_id) if client_id else None,
                "entity_level": entity_level,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

        try:
            comparisons = await load_entity_comparisons(
                self.db,
                organization_id=organization_id,
                client_id=client_id,
                window=window,
                platform=platform,
                entity_level=entity_level,
            )
            account_avg_cpl, account_avg_roas = self._account_averages(comparisons)

            created = 0
            updated = 0
            skipped_insufficient = 0
            signals_detected = 0

            for comparison in comparisons:
                signals = detect_signals(
                    comparison,
                    settings=settings,
                    account_avg_cpl=account_avg_cpl,
                    account_avg_roas=account_avg_roas,
                )
                if comparison.insufficient_data:
                    skipped_insufficient += 1
                signals_detected += len(signals)
                for signal in signals:
                    confidence = score_confidence(comparison, signal, settings=settings)
                    if use_ai_explanation:
                        explanation, source = await explain_recommendation(
                            comparison, signal, confidence=float(confidence)
                        )
                    else:
                        explanation = deterministic_explanation(comparison, signal)
                        source = "deterministic"
                    result = await self._upsert_recommendation(
                        comparison=comparison,
                        signal=signal,
                        confidence=confidence,
                        explanation=explanation,
                        explanation_source=source,
                    )
                    if result == "created":
                        created += 1
                    elif result == "updated":
                        updated += 1

            await write_audit(
                self.db,
                action="analytics.analysis.completed",
                organization_id=organization_id,
                user_id=actor_user_id,
                resource_type="analytics_analysis",
                resource_id=str(window_days),
                details={
                    "trigger": trigger,
                    "window_days": window_days,
                    "entities": len(comparisons),
                    "signals_detected": signals_detected,
                    "recommendations_created": created,
                    "recommendations_updated": updated,
                    "skipped_insufficient": skipped_insufficient,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
            await self.db.flush()
            return {
                "success": True,
                "window_days": window_days,
                "entities_analyzed": len(comparisons),
                "signals_detected": signals_detected,
                "recommendations_created": created,
                "recommendations_updated": updated,
                "skipped_insufficient": skipped_insufficient,
                "window": {
                    "current_start": window.current_start.isoformat(),
                    "current_end": window.current_end.isoformat(),
                    "previous_start": window.previous_start.isoformat(),
                    "previous_end": window.previous_end.isoformat(),
                },
            }
        except Exception as exc:
            await write_audit(
                self.db,
                action="analytics.analysis.failed",
                organization_id=organization_id,
                user_id=actor_user_id,
                resource_type="analytics_analysis",
                resource_id=str(window_days),
                details={
                    "trigger": trigger,
                    "message": str(exc)[:300],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
            await self.db.flush()
            raise

    def _account_averages(
        self, comparisons: list[EntityPeriodComparison]
    ) -> tuple[float | None, float | None]:
        cpl_values: list[float] = []
        roas_values: list[float] = []
        for comparison in comparisons:
            cur = comparison.current.as_dict()
            if cur.get("cpl") is not None and float(cur["spend"]) > 0:
                cpl_values.append(float(cur["cpl"]))
            if cur.get("roas") is not None and float(cur["spend"]) > 0:
                roas_values.append(float(cur["roas"]))
        avg_cpl = sum(cpl_values) / len(cpl_values) if cpl_values else None
        avg_roas = sum(roas_values) / len(roas_values) if roas_values else None
        return avg_cpl, avg_roas

    async def _upsert_recommendation(
        self,
        *,
        comparison: EntityPeriodComparison,
        signal,
        confidence: Decimal,
        explanation: str,
        explanation_source: str,
    ) -> str:
        settings = get_settings()
        fingerprint = recommendation_fingerprint(
            organization_id=comparison.organization_id,
            platform=comparison.platform,
            entity_level=comparison.entity_level,
            external_account_id=comparison.external_account_id,
            external_campaign_id=comparison.external_campaign_id,
            external_ad_set_id=comparison.external_ad_set_id,
            external_ad_id=comparison.external_ad_id,
            recommendation_type=signal.recommendation_type,
            metric=signal.metric,
            window_days=comparison.window.days,
            window_current_end=comparison.window.current_end.isoformat(),
        )
        existing = await self.db.scalar(
            select(PerformanceRecommendation).where(
                PerformanceRecommendation.organization_id == comparison.organization_id,
                PerformanceRecommendation.fingerprint == fingerprint,
            )
        )
        cur = comparison.current.as_dict()
        prev = comparison.previous.as_dict()
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=settings.performance_recommendation_ttl_days)
        payload = {
            "client_id": comparison.client_id,
            "platform": comparison.platform,
            "entity_level": comparison.entity_level,
            "external_account_id": comparison.external_account_id,
            "external_campaign_id": comparison.external_campaign_id,
            "external_ad_set_id": comparison.external_ad_set_id,
            "external_ad_id": comparison.external_ad_id,
            "recommendation_type": signal.recommendation_type,
            "severity": signal.severity,
            "title": signal.title,
            "explanation": explanation,
            "evidence": signal.evidence,
            "affected_metrics": [signal.metric],
            "current_values": {k: cur.get(k) for k in ("spend", "impressions", "clicks", "conversions", "leads", "revenue", "ctr", "cpc", "cpm", "cpl", "cpa", "roas")},
            "comparison_values": {k: prev.get(k) for k in ("spend", "impressions", "clicks", "conversions", "leads", "revenue", "ctr", "cpc", "cpm", "cpl", "cpa", "roas")},
            "percentage_changes": comparison.percentage_changes,
            "confidence": confidence,
            "suggested_action": {
                **signal.suggested_action,
                "informational_only": True,
                "execution_disabled": True,
            },
            "signal_category": signal.category,
            "analysis_window_days": comparison.window.days,
            "window_current_start": comparison.window.current_start,
            "window_current_end": comparison.window.current_end,
            "window_previous_start": comparison.window.previous_start,
            "window_previous_end": comparison.window.previous_end,
            "explanation_source": explanation_source,
            "expires_at": expires,
        }

        if existing is None:
            row = PerformanceRecommendation(
                organization_id=comparison.organization_id,
                fingerprint=fingerprint,
                status=PerformanceRecommendationStatus.new,
                **payload,
            )
            self.db.add(row)
            await self.db.flush()
            await write_audit(
                self.db,
                action="analytics.recommendation.created",
                organization_id=comparison.organization_id,
                user_id=None,
                resource_type="performance_recommendation",
                resource_id=str(row.id),
                details={
                    "recommendation_type": signal.recommendation_type,
                    "severity": signal.severity,
                    "platform": comparison.platform,
                    "external_campaign_id": comparison.external_campaign_id,
                    "confidence": float(confidence),
                    "informational_only": True,
                },
            )
            return "created"

        # Refresh analysis fields but preserve human lifecycle decisions
        if existing.status in {
            PerformanceRecommendationStatus.rejected,
            PerformanceRecommendationStatus.expired,
        }:
            return "skipped"
        for key, value in payload.items():
            setattr(existing, key, value)
        await self.db.flush()
        return "updated"

    async def list_recommendations(
        self,
        *,
        organization_id: UUID,
        client_id: UUID | None = None,
        platform: str | None = None,
        severity: str | None = None,
        recommendation_type: str | None = None,
        status: str | None = None,
        window_days: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[PerformanceRecommendation], int]:
        from sqlalchemy import func

        await self.expire_due(organization_id)

        filters = [PerformanceRecommendation.organization_id == organization_id]
        if client_id is not None:
            client = await self.db.get(Client, client_id)
            if client is None or client.organization_id != organization_id:
                return [], 0
            filters.append(PerformanceRecommendation.client_id == client_id)
        if platform:
            filters.append(PerformanceRecommendation.platform == platform.strip().lower())
        if severity:
            filters.append(PerformanceRecommendation.severity == severity.strip().upper())
        if recommendation_type:
            filters.append(PerformanceRecommendation.recommendation_type == recommendation_type.strip().upper())
        if status:
            try:
                filters.append(
                    PerformanceRecommendation.status
                    == PerformanceRecommendationStatus(status.strip().upper())
                )
            except ValueError:
                return [], 0
        if window_days:
            filters.append(PerformanceRecommendation.analysis_window_days == window_days)

        total = int(
            await self.db.scalar(
                select(func.count()).select_from(PerformanceRecommendation).where(*filters)
            )
            or 0
        )
        rows = list(
            (
                await self.db.scalars(
                    select(PerformanceRecommendation)
                    .where(*filters)
                    .order_by(PerformanceRecommendation.created_at.desc())
                    .offset(max(0, offset))
                    .limit(max(1, min(limit, 200)))
                )
            ).all()
        )
        return rows, total

    async def get_recommendation(
        self, *, organization_id: UUID, recommendation_id: UUID
    ) -> PerformanceRecommendation | None:
        await self.expire_due(organization_id)
        return await self.db.scalar(
            select(PerformanceRecommendation).where(
                PerformanceRecommendation.id == recommendation_id,
                PerformanceRecommendation.organization_id == organization_id,
            )
        )

    async def update_status(
        self,
        *,
        organization_id: UUID,
        recommendation_id: UUID,
        status: PerformanceRecommendationStatus,
        actor_user_id: UUID | None = None,
    ) -> PerformanceRecommendation | None:
        row = await self.get_recommendation(
            organization_id=organization_id, recommendation_id=recommendation_id
        )
        if row is None:
            return None
        # APPROVED does not execute anything — analysis-only lifecycle.
        row.status = status
        if status in {
            PerformanceRecommendationStatus.reviewed,
            PerformanceRecommendationStatus.approved,
            PerformanceRecommendationStatus.rejected,
        }:
            row.reviewed_at = datetime.now(timezone.utc)
        await write_audit(
            self.db,
            action="analytics.recommendation.status_changed",
            organization_id=organization_id,
            user_id=actor_user_id,
            resource_type="performance_recommendation",
            resource_id=str(row.id),
            details={
                "status": status.value,
                "informational_only": True,
                "execution_disabled": True,
            },
        )
        await self.db.flush()
        return row

    async def expire_due(self, organization_id: UUID) -> int:
        now = datetime.now(timezone.utc)
        rows = list(
            (
                await self.db.scalars(
                    select(PerformanceRecommendation).where(
                        PerformanceRecommendation.organization_id == organization_id,
                        PerformanceRecommendation.status.in_(
                            [
                                PerformanceRecommendationStatus.new,
                                PerformanceRecommendationStatus.reviewed,
                            ]
                        ),
                        PerformanceRecommendation.expires_at.is_not(None),
                        PerformanceRecommendation.expires_at < now,
                    )
                )
            ).all()
        )
        for row in rows:
            row.status = PerformanceRecommendationStatus.expired
        if rows:
            await self.db.flush()
        return len(rows)
