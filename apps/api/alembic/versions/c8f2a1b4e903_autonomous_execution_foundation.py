"""Autonomous execution foundation — action idempotency and platform external IDs.

Revision ID: c8f2a1b4e903
Revises: a17c5e8b4d90
Create Date: 2026-08-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c8f2a1b4e903"
down_revision: Union[str, None] = "a17c5e8b4d90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_actions", sa.Column("idempotency_key", sa.String(length=255), nullable=True))
    op.add_column("ai_actions", sa.Column("external_id", sa.String(length=255), nullable=True))
    op.create_index(
        "uq_ai_actions_org_idempotency",
        "ai_actions",
        ["organization_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.add_column("ad_sets", sa.Column("external_id", sa.String(length=255), nullable=True))
    op.add_column("ad_sets", sa.Column("platform", sa.String(length=64), nullable=True))
    op.add_column(
        "ad_sets",
        sa.Column("created_by_action_id", sa.UUID(), sa.ForeignKey("ai_actions.id", ondelete="SET NULL"), nullable=True),
    )

    op.add_column("ads", sa.Column("external_id", sa.String(length=255), nullable=True))
    op.add_column("ads", sa.Column("platform", sa.String(length=64), nullable=True))
    op.add_column(
        "ads",
        sa.Column("created_by_action_id", sa.UUID(), sa.ForeignKey("ai_actions.id", ondelete="SET NULL"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ads", "created_by_action_id")
    op.drop_column("ads", "platform")
    op.drop_column("ads", "external_id")
    op.drop_column("ad_sets", "created_by_action_id")
    op.drop_column("ad_sets", "platform")
    op.drop_column("ad_sets", "external_id")
    op.drop_index("uq_ai_actions_org_idempotency", table_name="ai_actions")
    op.drop_column("ai_actions", "external_id")
    op.drop_column("ai_actions", "idempotency_key")
