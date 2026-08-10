"""Lightweight column adds for local SQLite (create_all does not ALTER)."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


TABLE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "autonomy_settings": [
        ("maximum_actions_per_day", "INTEGER DEFAULT 50"),
        ("max_ai_iterations", "INTEGER DEFAULT 1"),
        ("max_ai_actions_per_cycle", "INTEGER DEFAULT 5"),
        ("max_execution_time", "INTEGER DEFAULT 300"),
        ("max_failures_per_cycle", "INTEGER DEFAULT 3"),
    ],
    "creative_assets": [
        ("mime_type", "VARCHAR(128)"),
        ("width", "INTEGER"),
        ("height", "INTEGER"),
        ("duration_seconds", "INTEGER"),
        ("provider_asset_id", "VARCHAR(255)"),
    ],
    "image_jobs": [
        ("campaign_id", "CHAR(32)"),
        ("provider_job_id", "VARCHAR(255)"),
        ("aspect_ratio", "VARCHAR(16) DEFAULT '1:1'"),
        ("width", "INTEGER"),
        ("height", "INTEGER"),
        ("idempotency_key", "VARCHAR(255)"),
        ("error_code", "VARCHAR(120)"),
        ("retryable", "BOOLEAN DEFAULT 0"),
        ("attempts", "INTEGER DEFAULT 0"),
    ],
        "background_jobs": [
            ("locked_by", "VARCHAR(128)"),
            ("lease_expires_at", "DATETIME"),
            ("heartbeat_at", "DATETIME"),
            ("dedupe_key", "VARCHAR(255)"),
        ],
    "leads": [
        ("external_id", "VARCHAR(255)"),
        ("source_metadata", "JSON"),
    ],
    "video_jobs": [
        ("campaign_id", "CHAR(32)"),
        ("provider_job_id", "VARCHAR(255)"),
        ("aspect_ratio", "VARCHAR(16) DEFAULT '9:16'"),
        ("duration_seconds", "INTEGER DEFAULT 10"),
        ("idempotency_key", "VARCHAR(255)"),
        ("error_code", "VARCHAR(120)"),
        ("retryable", "BOOLEAN DEFAULT 0"),
        ("attempts", "INTEGER DEFAULT 0"),
    ],
}


async def ensure_sqlite_columns(conn: AsyncConnection) -> None:
    dialect = conn.dialect.name
    if dialect != "sqlite":
        return
    for table, columns in TABLE_COLUMNS.items():
        exists = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
            {"t": table},
        )
        if not exists.fetchone():
            continue
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        existing = {row[1] for row in result.fetchall()}
        for name, ddl in columns:
            if name not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
