"""Reconcile JSON column nullability in the billing and usage tables

Revision ID: a17c5e8b4d90
Revises: f94b6d3a8c21
Create Date: 2026-08-11

`alembic check` reported permanent drift on five JSON columns: the models type
them as `Mapped[dict]` (NOT NULL) while the P1 migrations created them nullable.
Drift that is always present is worse than useless — it hides the one real
difference that matters during a release — so it is corrected here rather than
left for the next author to squint at.

The nullability is not cosmetic. Each of these columns is read as a mapping by
application code (`record.details["metric"]`, `plan.limits.get(...)`). A NULL
arriving from a raw insert or a data migration would raise a TypeError far from
the cause. Existing NULLs are backfilled with an empty object before the
constraint is applied, so this is safe on a populated database.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a17c5e8b4d90"
down_revision: Union[str, None] = "f94b6d3a8c21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMPTY_DICT = sa.text("'{}'")

#: table, column — all dict-valued, so an empty object is the correct default.
COLUMNS = [
    ("billing_events", "details"),
    ("organization_subscriptions", "limit_overrides"),
    ("plans", "limits"),
    ("plans", "features"),
    ("usage_records", "details"),
]


def upgrade() -> None:
    for table, column in COLUMNS:
        # Backfill first: ALTER SET NOT NULL fails outright if any row is NULL.
        op.execute(f"UPDATE {table} SET {column} = '{{}}' WHERE {column} IS NULL")
        op.alter_column(
            table,
            column,
            existing_type=sa.JSON(),
            nullable=False,
            server_default=EMPTY_DICT,
        )


def downgrade() -> None:
    for table, column in COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.JSON(),
            nullable=True,
            server_default=None,
        )
