"""
Shared test bootstrap.

The suite exercises the real app against a local SQLite database. httpx's
ASGITransport does not run FastAPI lifespan, so the schema has to be created
here or tests silently run against whatever happens to be on disk.
"""

from __future__ import annotations

import asyncio

import pytest

from app.db.base import Base
from app.db.session import engine
import app.models  # noqa: F401 — register all tables before create_all


@pytest.fixture(scope="session", autouse=True)
def _create_schema() -> None:
    async def _run() -> None:
        from app.db.schema_migrate import ensure_sqlite_columns

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await ensure_sqlite_columns(conn)
        await engine.dispose()

    asyncio.run(_run())


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """
    Rate-limit counters are process-global and every test logs in from the same
    fake client address, so without this the suite throttles itself: a test that
    happens to run late gets a 429 instead of a token. Tests that assert on
    limiting install their own backend.
    """
    from app.security.rate_limit import set_rate_limit_backend

    set_rate_limit_backend(None)
    yield
    set_rate_limit_backend(None)


@pytest.fixture(autouse=True)
def _reset_object_storage():
    """The storage backend is a cached singleton; a test that swaps it must not
    leak that choice into the next one."""
    from app.storage import set_object_storage

    set_object_storage(None)
    yield
    set_object_storage(None)


@pytest.fixture(autouse=True)
async def _dispose_engine_between_tests():
    """
    pytest-asyncio gives each test a fresh event loop, but the engine is a module
    global with a connection pool. Pooled connections bound to a closed loop fail
    on the next test (visibly with asyncpg), so drop the pool after each test.
    """
    yield
    await engine.dispose()
