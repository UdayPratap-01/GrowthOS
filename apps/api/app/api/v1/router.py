from fastapi import APIRouter

from app.api.v1 import (
    analytics,
    assistant,
    auth,
    autopilot,
    billing,
    campaign_generation,
    campaigns,
    clients,
    competitors,
    content,
    creative,
    dashboard,
    integrations,
    jobs,
    usage,
    lead_scoring,
    leads,
    recommendations,
    reports,
    strategies,
    webhooks,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(clients.router)
api_router.include_router(dashboard.router)
api_router.include_router(strategies.router)
api_router.include_router(content.router)
api_router.include_router(leads.router)
api_router.include_router(assistant.router)
api_router.include_router(integrations.router)
api_router.include_router(analytics.router)
api_router.include_router(campaigns.router)
api_router.include_router(campaign_generation.router)
api_router.include_router(recommendations.router)
api_router.include_router(reports.router)
api_router.include_router(competitors.router)
api_router.include_router(lead_scoring.router)
api_router.include_router(autopilot.router)
api_router.include_router(creative.router)
api_router.include_router(webhooks.router)
api_router.include_router(jobs.router)
api_router.include_router(usage.router)
api_router.include_router(billing.router)
