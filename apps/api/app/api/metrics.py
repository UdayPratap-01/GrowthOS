"""
Metrics exposition.

Provider-independent on purpose. The counters live in-process
(`app/observability/metrics.py`) and are exposed in Prometheus text format and
as JSON, so anything that scrapes HTTP — Prometheus, Grafana Agent, Datadog's
OpenMetrics check, a shell script — can read them without the application
carrying a vendor SDK. Swapping monitoring vendors becomes a scraper
configuration change rather than a code change.

Every number comes from an event the application actually handled. There is no
synthetic traffic and no placeholder series: a metric that has never been
observed is absent rather than reported as a confident zero.

Access: the endpoint sits outside the authenticated API — a scraper has no user
account — so in production it requires a bearer token. Request rates, error
ratios, provider names and route shapes are useful reconnaissance, and the
startup checks refuse to boot a production instance without `METRICS_TOKEN` set.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Response, status

from app.core.config import get_settings
from app.observability import metrics

router = APIRouter(tags=["monitoring"])


def _authorize(authorization: str | None) -> None:
    settings = get_settings()
    token = settings.metrics_token.strip()

    if not token:
        if settings.is_production:
            # Should be unreachable — startup refuses without a token — but a
            # misconfiguration must close the endpoint, not open it.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="NOT_FOUND: Metrics are not exposed."
            )
        return

    presented = (authorization or "").removeprefix("Bearer ").strip()
    # Compared in constant time: a token comparison that returns early leaks its
    # length and prefix to anyone who can time the response.
    import hmac

    if not presented or not hmac.compare_digest(presented, token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="UNAUTHENTICATED: A valid metrics token is required.",
        )


@router.get("/metrics")
async def prometheus_metrics(authorization: str | None = Header(default=None)) -> Response:
    _authorize(authorization)
    return Response(
        content=metrics.prometheus_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/metrics.json")
async def json_metrics(authorization: str | None = Header(default=None)) -> dict:
    """Same data, for humans and for dashboards that prefer JSON."""
    _authorize(authorization)
    return metrics.snapshot()
