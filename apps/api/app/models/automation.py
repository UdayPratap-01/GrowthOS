"""Phase 5 automation models — actions, autonomy, creatives, optimization."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    AIActionStatus,
    AIActionType,
    AutonomyMode,
    HealthCategory,
    JobStatus,
    Priority,
    RiskLevel,
)


class AutonomySettings(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "autonomy_settings"

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True
    )
    autonomy_mode: Mapped[AutonomyMode] = mapped_column(
        Enum(AutonomyMode, name="autonomy_mode", native_enum=False), default=AutonomyMode.copilot
    )
    maximum_daily_ad_spend: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("500"))
    maximum_campaign_budget: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("2000"))
    maximum_budget_increase_percentage: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("15"))
    maximum_budget_decrease_percentage: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("30"))
    maximum_campaigns_per_day: Mapped[int] = mapped_column(Integer, default=3)
    maximum_creatives_per_day: Mapped[int] = mapped_column(Integer, default=10)
    maximum_posts_per_day: Mapped[int] = mapped_column(Integer, default=5)
    maximum_actions_per_day: Mapped[int] = mapped_column(Integer, default=50)
    require_approval_for_financial_actions: Mapped[bool] = mapped_column(Boolean, default=True)
    require_approval_for_publishing: Mapped[bool] = mapped_column(Boolean, default=True)
    require_approval_for_campaign_creation: Mapped[bool] = mapped_column(Boolean, default=True)
    allowed_platforms: Mapped[list] = mapped_column(JSON, default=list)
    allowed_actions: Mapped[list] = mapped_column(JSON, default=list)
    automation_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    max_ai_iterations: Mapped[int] = mapped_column(Integer, default=1)
    max_ai_actions_per_cycle: Mapped[int] = mapped_column(Integer, default=5)
    max_execution_time: Mapped[int] = mapped_column(Integer, default=300)  # seconds
    max_failures_per_cycle: Mapped[int] = mapped_column(Integer, default=3)


class AIAction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_actions"
    __table_args__ = (
        Index(
            "uq_ai_actions_org_idempotency",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
            sqlite_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True
    )
    action_type: Mapped[AIActionType] = mapped_column(
        Enum(AIActionType, name="ai_action_type", native_enum=False), index=True
    )
    agent: Mapped[str] = mapped_column(String(64), default="orchestrator")
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    target_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    expected_impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    risk_level: Mapped[RiskLevel] = mapped_column(
        Enum(RiskLevel, name="ai_action_risk", native_enum=False), default=RiskLevel.medium
    )
    priority: Mapped[Priority] = mapped_column(
        Enum(Priority, name="ai_action_priority", native_enum=False), default=Priority.medium
    )
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[AIActionStatus] = mapped_column(
        Enum(AIActionStatus, name="ai_action_status", native_enum=False),
        default=AIActionStatus.pending,
        index=True,
    )
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    previous_state: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    demo_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ActionExecution(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "action_executions"

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    action_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ai_actions.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[AIActionStatus] = mapped_column(
        Enum(AIActionStatus, name="execution_status", native_enum=False), default=AIActionStatus.executing
    )
    platform_response: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class CreativeAsset(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "creative_assets"

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    campaign_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True
    )
    # P2-A: which hypothesis this file was produced to test. Nullable because
    # assets generated by the standalone media endpoints have no concept.
    concept_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("creative_concepts.id", ondelete="SET NULL"), nullable=True
    )
    variation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("creative_variations.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(64), default="concept")  # concept|image|video|copy
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True)
    aspect_ratio: Mapped[str | None] = mapped_column(String(16), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_asset_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="draft")
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    data_source: Mapped[str] = mapped_column(String(32), default="demo")


class ImageJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "image_jobs"

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=True
    )
    campaign_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True
    )
    creative_asset_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("creative_assets.id", ondelete="SET NULL"), nullable=True
    )
    concept_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("creative_concepts.id", ondelete="SET NULL"), nullable=True
    )
    variation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("creative_variations.id", ondelete="SET NULL"), nullable=True
    )
    run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaign_generation_runs.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(64), default="none")
    provider_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="1:1")
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="image_job_status", native_enum=False), default=JobStatus.queued
    )
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class VideoJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "video_jobs"

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=True
    )
    campaign_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True
    )
    creative_asset_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("creative_assets.id", ondelete="SET NULL"), nullable=True
    )
    concept_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("creative_concepts.id", ondelete="SET NULL"), nullable=True
    )
    variation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("creative_variations.id", ondelete="SET NULL"), nullable=True
    )
    run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaign_generation_runs.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(64), default="none")
    provider_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="9:16")
    duration_seconds: Mapped[int] = mapped_column(Integer, default=10)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="video_job_status", native_enum=False), default=JobStatus.queued
    )
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class ScheduledPost(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scheduled_posts"

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    social_post_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("social_posts.id", ondelete="SET NULL"), nullable=True
    )
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), default="scheduled", index=True)
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    publish_result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ai_actions.id", ondelete="SET NULL"), nullable=True
    )


class OptimizationRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "optimization_rules"

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    condition: Mapped[dict] = mapped_column(JSON, default=dict)
    action_template: Mapped[dict] = mapped_column(JSON, default=dict)
    priority: Mapped[Priority] = mapped_column(
        Enum(Priority, name="opt_rule_priority", native_enum=False), default=Priority.medium
    )


class OptimizationEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "optimization_events"

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=True
    )
    rule_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("optimization_rules.id", ondelete="SET NULL"), nullable=True
    )
    action_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ai_actions.id", ondelete="SET NULL"), nullable=True
    )
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[Priority] = mapped_column(
        Enum(Priority, name="opt_event_priority", native_enum=False), default=Priority.medium
    )
    status: Mapped[str] = mapped_column(String(64), default="open")


class CampaignHealth(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "campaign_health"

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    campaign_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    score: Mapped[int] = mapped_column(Integer, default=0)
    category: Mapped[HealthCategory] = mapped_column(
        Enum(HealthCategory, name="campaign_health_category", native_enum=False),
        default=HealthCategory.needs_attention,
    )
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    metrics_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    data_source: Mapped[str] = mapped_column(String(32), default="demo")


class BackgroundJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "background_jobs"

    organization_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    job_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    # Idempotency: enqueuing twice with the same key returns the first job
    # instead of duplicating the work. Unique so a race loses at the database.
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="background_job_status", native_enum=False), default=JobStatus.queued, index=True
    )
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Lease fields. A running job is owned by exactly one worker until its lease
    # expires; an expired lease means the worker died and the job is reclaimable.
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AutopilotRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Multi-step campaign builder / one-click autopilot progress tracking."""

    __tablename__ = "autopilot_runs"

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    run_type: Mapped[str] = mapped_column(String(64), default="marketing_autopilot")  # marketing_autopilot | campaign_build
    status: Mapped[str] = mapped_column(String(32), default="RUNNING", index=True)
    goal: Mapped[str] = mapped_column(String(255), default="Generate Leads")
    budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    duration_days: Mapped[int] = mapped_column(Integer, default=30)
    platforms: Mapped[list] = mapped_column(JSON, default=list)
    autonomy_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    request: Mapped[dict] = mapped_column(JSON, default=dict)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    action_ids: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    demo_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
