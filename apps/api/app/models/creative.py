"""
P2-A AI Creative & Campaign Engine — persisted generation artefacts.

Four entities, each one thing the generator produces and a reviewer needs to see
again later:

    CampaignGenerationRun   one press of "Generate Campaign"; owns the progress
                            the UI renders and the terminal outcome
    CampaignBrief           the structured input the agents agreed on, kept so a
                            regeneration starts from the same brief
    CreativeConcept         one marketing hypothesis: angle, copy, visual spec
    CreativeVariation       a deliberate single-axis change to a concept

Media assets are *not* re-modelled here. `CreativeAsset`, `ImageJob` and
`VideoJob` already carry the real provider/storage pipeline, so concepts and
variations point at them instead of duplicating the columns.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CampaignGenerationRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    One campaign generation, from request to reviewable package.

    `stages` holds the real per-stage state the worker writes as it goes —
    strategy, copy, concepts, images 2/3, videos 1/2. The frontend renders it
    verbatim; nothing about progress is interpolated or estimated client-side.
    """

    __tablename__ = "campaign_generation_runs"

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    brief_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaign_briefs.id", ondelete="SET NULL"), nullable=True
    )
    campaign_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True
    )
    requested_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: QUEUED | GENERATING | READY_FOR_REVIEW | FAILED | CANCELLED
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    platform: Mapped[str] = mapped_column(String(64), default="meta")
    objective: Mapped[str] = mapped_column(String(64), default="lead_generation")
    request: Mapped[dict] = mapped_column(JSON, default=dict)
    stages: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Every fact the generator could not establish from stored data. Surfaced
    #: to the reviewer instead of being papered over with a plausible number.
    data_limitations: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    background_job_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("background_jobs.id", ondelete="SET NULL"), nullable=True
    )
    #: Ties every metered unit of this run to one logical operation, so a retry
    #: of the same run does not bill a second time.
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_quantity: Mapped[int] = mapped_column(Integer, default=0)
    video_quantity: Mapped[int] = mapped_column(Integer, default=0)
    concept_quantity: Mapped[int] = mapped_column(Integer, default=3)
    variation_quantity: Mapped[int] = mapped_column(Integer, default=0)
    demo_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_generation_run_idempotency"),
        Index("ix_campaign_generation_runs_org_client", "organization_id", "client_id"),
    )


class CampaignBrief(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    The structured brief the campaign was generated from.

    Free-text fields are the agent's words; `data_limitations` is the honest
    inverse — what the brief could not be grounded in.
    """

    __tablename__ = "campaign_briefs"

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    campaign_name: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), default="meta")
    objective: Mapped[str] = mapped_column(String(64), default="lead_generation")
    offer: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    pain_points: Mapped[list] = mapped_column(JSON, default=list)
    value_proposition: Mapped[str | None] = mapped_column(Text, nullable=True)
    messaging_angle: Mapped[str | None] = mapped_column(Text, nullable=True)
    tone: Mapped[str | None] = mapped_column(String(120), nullable=True)
    brand_constraints: Mapped[list] = mapped_column(JSON, default=list)
    total_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    daily_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    monthly_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    success_metrics: Mapped[list] = mapped_column(JSON, default=list)
    creative_direction: Mapped[str | None] = mapped_column(Text, nullable=True)
    cta: Mapped[str | None] = mapped_column(String(120), nullable=True)
    data_limitations: Mapped[list] = mapped_column(JSON, default=list)
    #: Full 13-section strategy document from CampaignStrategyAgent.
    strategy: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Snapshot of the ClientContext the agents actually saw, so a brief can be
    #: audited later even after the client record changes.
    client_context_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    data_source: Mapped[str] = mapped_column(String(32), default="live")


class CreativeConcept(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    One marketing hypothesis: an angle plus the copy and visual spec to test it.

    Three concepts on the same campaign are meant to be three different bets,
    not three phrasings of one. `angle` and `hypothesis` are what make that
    checkable by a reviewer.
    """

    __tablename__ = "creative_concepts"

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    brief_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaign_briefs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    campaign_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaign_generation_runs.id", ondelete="SET NULL"), nullable=True
    )
    #: Stable, human-referenceable label within a campaign: "A", "B", "C".
    reference: Mapped[str] = mapped_column(String(16), default="A")
    angle: Mapped[str] = mapped_column(Text, nullable=False)
    hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    headline: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cta: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tone: Mapped[str | None] = mapped_column(String(120), nullable=True)
    audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    objective: Mapped[str | None] = mapped_column(String(64), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Visual specification: composition, subject, environment, lighting, style,
    #: brand elements, text overlay. One JSON column because it is read and
    #: written as a whole and never queried field-by-field.
    visual_direction: Mapped[dict] = mapped_column(JSON, default=dict)
    image_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    negative_constraints: Mapped[list] = mapped_column(JSON, default=list)
    aspect_ratios: Mapped[list] = mapped_column(JSON, default=list)
    #: DRAFT | READY | ARCHIVED
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data_limitations: Mapped[list] = mapped_column(JSON, default=list)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    data_source: Mapped[str] = mapped_column(String(32), default="live")

    __table_args__ = (
        Index("ix_creative_concepts_org_campaign", "organization_id", "campaign_id"),
    )


class CreativeVariation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    A deliberate single-axis change to a parent concept.

    `axis` records *what* was changed (hook, visual, offer, cta, tone,
    composition, format, audience_angle) and `hypothesis` records *why*. A
    variation that cannot state both is a synonym substitution, which this
    system is explicitly not for.
    """

    __tablename__ = "creative_variations"

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    parent_concept_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("creative_concepts.id", ondelete="CASCADE"), index=True
    )
    campaign_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaign_generation_runs.id", ondelete="SET NULL"), nullable=True
    )
    reference: Mapped[str] = mapped_column(String(16), default="B")
    #: hook | visual | offer | cta | tone | composition | format | audience_angle
    axis: Mapped[str] = mapped_column(String(32), default="hook", index=True)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    #: image | video | copy — what has to be produced to test this variation.
    creative_type: Mapped[str] = mapped_column(String(32), default="copy")
    hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    headline: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cta: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tone: Mapped[str | None] = mapped_column(String(120), nullable=True)
    audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    aspect_ratio: Mapped[str | None] = mapped_column(String(16), nullable=True)
    visual_direction: Mapped[dict] = mapped_column(JSON, default=dict)
    image_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    negative_constraints: Mapped[list] = mapped_column(JSON, default=list)
    #: DRAFT | READY | ARCHIVED
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    data_source: Mapped[str] = mapped_column(String(32), default="live")
