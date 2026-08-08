from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import engine
import app.models  # noqa: F401 — register models


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Phase 1 convenience: create tables on startup. Alembic remains available for migrations.
    from app.db.schema_migrate import ensure_sqlite_columns

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await ensure_sqlite_columns(conn)
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "growthos-api"}

    return app


app = create_app()
