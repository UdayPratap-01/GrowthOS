"""Lightweight column adds for local SQLite (create_all does not ALTER)."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


AUTONOMY_COLUMNS: list[tuple[str, str]] = [
    ("maximum_actions_per_day", "INTEGER DEFAULT 50"),
    ("max_ai_iterations", "INTEGER DEFAULT 1"),
    ("max_ai_actions_per_cycle", "INTEGER DEFAULT 5"),
    ("max_execution_time", "INTEGER DEFAULT 300"),
    ("max_failures_per_cycle", "INTEGER DEFAULT 3"),
]


async def ensure_sqlite_columns(conn: AsyncConnection) -> None:
    dialect = conn.dialect.name
    if dialect != "sqlite":
        return
    result = await conn.execute(text("PRAGMA table_info(autonomy_settings)"))
    existing = {row[1] for row in result.fetchall()}
    for name, ddl in AUTONOMY_COLUMNS:
        if name not in existing:
            await conn.execute(text(f"ALTER TABLE autonomy_settings ADD COLUMN {name} {ddl}"))
