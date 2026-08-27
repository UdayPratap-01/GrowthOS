import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.metrics import router as metrics_router
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.db.base import Base
from app.db.session import engine
from app.observability.logging import configure_logging
from app.observability.middleware import RequestContextMiddleware
from app.observability.security_headers import SecurityHeadersMiddleware
import app.models  # noqa: F401 — register models


logger = logging.getLogger("growthos.startup")


@asynccontextmanager
async def lifespan(_: FastAPI):
    from app.core.startup_checks import validate_configuration
    from app.observability.logging import configure_logging

    configure_logging(service="growthos-api")

    # Refuse to serve traffic with an unsafe configuration.
    validate_configuration()

    settings = get_settings()
    logger.info(
        "API starting",
        extra={
            "event": "startup",
            "environment": settings.env,
            "storage_backend": settings.storage_backend,
            "ai_provider": settings.ai_provider,
            "inline_jobs": settings.should_run_jobs_inline,
            "shared_rate_limits": bool(settings.redis_url),
        },
    )
    if settings.should_auto_create_tables:
        # Development convenience only. Production applies Alembic migrations.
        from app.db.schema_migrate import ensure_sqlite_columns

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await ensure_sqlite_columns(conn)
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(service="growthos-api")
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

    # Added first so it is outermost: the request ID must exist before any other
    # middleware or handler runs, and must survive into the error response.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    # Unversioned and unauthenticated: probes are infrastructure, not API surface.
    app.include_router(health_router)
    app.include_router(metrics_router)
    register_exception_handlers(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """
        Retained for existing probes and the Dockerfile healthcheck. New
        deployments should use /health/live and /health/ready.
        """
        return {
            "status": "ok",
            "service": "growthos-api",
            "environment": settings.env,
            "demo_mode": str(settings.demo_mode).lower(),
        }

    return app


app = create_app()
