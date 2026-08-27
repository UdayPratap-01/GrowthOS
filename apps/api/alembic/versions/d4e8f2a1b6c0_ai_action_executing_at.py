"""AI action executing_at — stale execution recovery timestamp.

Revision ID: d4e8f2a1b6c0
Revises: c8f2a1b4e903
Create Date: 2026-08-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e8f2a1b6c0"
down_revision: Union[str, None] = "c8f2a1b4e903"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_actions",
        sa.Column("executing_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_actions", "executing_at")
