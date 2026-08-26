from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ConnectionStatus, DataSource


class SocialAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "social_accounts"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    connection_status: Mapped[ConnectionStatus] = mapped_column(
        Enum(ConnectionStatus, name="social_connection_status", native_enum=False), default=ConnectionStatus.not_connected
    )
    encrypted_credentials_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AdAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ad_accounts"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    connection_status: Mapped[ConnectionStatus] = mapped_column(
        Enum(ConnectionStatus, name="ad_connection_status", native_enum=False), default=ConnectionStatus.not_connected
    )
    encrypted_credentials_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Campaign(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "campaigns"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    ad_account_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("ad_accounts.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Platform delivery state (active, paused, …) for campaigns that exist on a
    #: platform. Deliberately separate from `review_status`: one describes what
    #: an ad platform is doing, the other what a human has agreed to internally,
    #: and collapsing them would lose the approval record the moment a campaign
    #: went live.
    status: Mapped[str] = mapped_column(String(64), default="active")
    #: Internal GrowthOS lifecycle:
    #: DRAFT → GENERATING → READY_FOR_REVIEW → APPROVED → READY_TO_PUBLISH,
    #: or → REJECTED. There is no PUBLISHED value: publishing is out of scope
    #: for P2-A and would require a confirmed external campaign id.
    review_status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    objective: Mapped[str | None] = mapped_column(String(120), nullable=True)
    brief_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaign_briefs.id", ondelete="SET NULL"), nullable=True
    )
    audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    daily_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    monthly_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    generated_by_ai: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Set only when an integration confirms an external campaign. Nothing in
    #: P2-A writes it; it exists so "published" can never be inferred from a
    #: status string alone.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejected_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    spend: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    data_source: Mapped[DataSource] = mapped_column(Enum(DataSource, name="campaign_data_source", native_enum=False), default=DataSource.demo)


class AdSet(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ad_sets"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="active")
    audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    daily_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    optimization: Mapped[str | None] = mapped_column(String(64), nullable=True)
    placements: Mapped[list] = mapped_column(JSON, default=list)
    spend: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by_action_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ai_actions.id", ondelete="SET NULL"), nullable=True
    )


class Ad(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ads"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    ad_set_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("ad_sets.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="active")
    concept_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("creative_concepts.id", ondelete="SET NULL"), nullable=True
    )
    variation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("creative_variations.id", ondelete="SET NULL"), nullable=True
    )
    creative_asset_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("creative_assets.id", ondelete="SET NULL"), nullable=True
    )
    headline: Mapped[str | None] = mapped_column(String(512), nullable=True)
    primary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    cta: Mapped[str | None] = mapped_column(String(120), nullable=True)
    destination: Mapped[str | None] = mapped_column(String(512), nullable=True)
    creative: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by_action_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("ai_actions.id", ondelete="SET NULL"), nullable=True
    )


class SocialPost(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "social_posts"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    main_copy: Mapped[str | None] = mapped_column(Text, nullable=True)
    cta: Mapped[str | None] = mapped_column(String(512), nullable=True)
    visual_concept: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_concept: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(64), default="draft")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    data_source: Mapped[DataSource] = mapped_column(Enum(DataSource, name="post_data_source", native_enum=False), default=DataSource.demo)


class ContentCalendar(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "content_calendar"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    social_post_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("social_posts.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="planned")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ContentAsset(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "content_assets"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class AnalyticsDaily(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "analytics_daily"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    spend: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    leads: Mapped[int] = mapped_column(Integer, default=0)
    revenue: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    data_source: Mapped[DataSource] = mapped_column(Enum(DataSource, name="analytics_daily_source", native_enum=False), default=DataSource.demo)


class AnalyticsCampaign(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "analytics_campaign"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    spend: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    leads: Mapped[int] = mapped_column(Integer, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    ctr: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    cpl: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    data_source: Mapped[DataSource] = mapped_column(Enum(DataSource, name="analytics_campaign_source", native_enum=False), default=DataSource.demo)


class Competitor(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "competitors"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    observations: Mapped[dict] = mapped_column(JSON, default=dict)
