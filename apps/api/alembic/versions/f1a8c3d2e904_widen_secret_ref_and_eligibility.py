"""Widen integrations.secret_ref and seo_schema eligibility_status for PostgreSQL.

Revision ID: f1a8c3d2e904
Revises: e9f7a5b3d263
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a8c3d2e904"
down_revision: Union[str, None] = "e9f7a5b3d263"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "integrations",
        "secret_ref",
        existing_type=sa.String(length=255),
        type_=sa.Text(),
        existing_nullable=True,
    )
    op.alter_column(
        "seo_schema_artifacts",
        "eligibility_status",
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "seo_schema_artifacts",
        "eligibility_status",
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
    op.alter_column(
        "integrations",
        "secret_ref",
        existing_type=sa.Text(),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
