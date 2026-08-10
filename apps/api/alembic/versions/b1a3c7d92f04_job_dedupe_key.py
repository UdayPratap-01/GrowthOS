"""P1-3: idempotent job enqueue via background_jobs.dedupe_key

Revision ID: b1a3c7d92f04
Revises: af352aece3bc
Create Date: 2026-08-10

The unique index is the actual idempotency guarantee: two API instances handling
a duplicate submit race at the database, and the loser reads the winner's row
instead of queueing the same generation twice.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b1a3c7d92f04"
down_revision: Union[str, None] = "af352aece3bc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("background_jobs", sa.Column("dedupe_key", sa.String(length=255), nullable=True))
    op.create_index(
        "ix_background_jobs_dedupe_key",
        "background_jobs",
        ["dedupe_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_background_jobs_dedupe_key", table_name="background_jobs")
    op.drop_column("background_jobs", "dedupe_key")
