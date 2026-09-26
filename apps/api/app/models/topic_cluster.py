"""Topic cluster persistence (M9.5)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TopicCluster(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "topic_clusters"
    __table_args__ = (
        UniqueConstraint("sync_id", "cluster_key", name="uq_topic_clusters_sync_cluster_key"),
    )

    sync_id: Mapped[UUID] = mapped_column(ForeignKey("search_console_syncs.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    site_url: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    cluster_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    topic_label: Mapped[str] = mapped_column(String(512), nullable=False)
    representative_query: Mapped[str] = mapped_column(Text, nullable=False)
    query_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_clicks: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    total_impressions: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    aggregate_ctr: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    weighted_average_position: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    opportunity_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    multi_page_signal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_singleton: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(16), nullable=False, default="v1", index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)


class TopicClusterQuery(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "topic_cluster_queries"
    __table_args__ = (
        UniqueConstraint("topic_cluster_id", "normalized_query", name="uq_topic_cluster_queries_cluster_norm"),
    )

    topic_cluster_id: Mapped[UUID] = mapped_column(ForeignKey("topic_clusters.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_query: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    similarity_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    membership_reason: Mapped[str] = mapped_column(String(64), nullable=False, default="jaccard_token_overlap")
    clicks: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    impressions: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    ctr: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    average_position: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    opportunity_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_representative: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class TopicClusterPage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "topic_cluster_pages"
    __table_args__ = (
        UniqueConstraint("topic_cluster_id", "page_url", name="uq_topic_cluster_pages_cluster_url"),
    )

    topic_cluster_id: Mapped[UUID] = mapped_column(ForeignKey("topic_clusters.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    page_url: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    clicks: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    impressions: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    ctr: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    average_position: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
