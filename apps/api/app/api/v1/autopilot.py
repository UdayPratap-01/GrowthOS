from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings as app_settings
from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.security.limits import ai_limit, campaign_execution_limit, media_limit
from app.models.enums import AIActionStatus, AIActionType, Priority, RiskLevel
from app.schemas.autopilot import (
    AIActionCreate,
    AIActionOut,
    ActionDecision,
    AutonomySettingsOut,
    AutonomySettingsUpdate,
    AutopilotCycleRequest,
    AutopilotCycleResult,
    AutopilotRunOut,
    AutopilotRunRequest,
    AutopilotSummary,
    CampaignBuildRequest,
    CampaignBuildResult,
    CampaignHealthOut,
    CampaignProposeRequest,
    CreativeAssetOut,
    CreativeGenerateRequest,
    CreativeVariationsRequest,
    DecisionLoopRequest,
    DecisionLoopResult,
    ImageGenerateRequest,
    OptimizationEventOut,
    OptimizationRuleIn,
    OptimizationRuleOut,
    PublishContentRequest,
    ScheduleContentRequest,
    VideoGenerateRequest,
)
from app.services.action_service import ActionService
from app.services.autonomy_service import AutonomyService
from app.services.autopilot_orchestrator_service import AutopilotOrchestratorService
from app.services.campaign_build_service import CampaignBuildService
from app.services.creative_service import CreativeService
from app.services.optimization_service import OptimizationService

router = APIRouter(prefix="/autopilot", tags=["autopilot"])


@router.get("/settings", response_model=AutonomySettingsOut)
async def get_settings(
    client_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> AutonomySettingsOut:
    return await AutonomyService(db).get_out(auth.organization_id, client_id)


@router.put("/settings", response_model=AutonomySettingsOut)
async def update_settings(
    data: AutonomySettingsUpdate,
    client_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(require_permission(Permission.autonomy_manage)),
    db: AsyncSession = Depends(get_db),
) -> AutonomySettingsOut:
    return await AutonomyService(db).update(auth.organization_id, data, client_id)


@router.get("/summary", response_model=AutopilotSummary)
async def summary(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> AutopilotSummary:
    return await ActionService(db).summary(auth.organization_id, demo_mode=auth.demo_mode)


@router.get("/actions", response_model=list[AIActionOut])
async def list_actions(
    client_id: UUID | None = Query(default=None),
    status: AIActionStatus | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[AIActionOut]:
    return await ActionService(db).list(auth.organization_id, client_id=client_id, status_filter=status)


@router.post("/actions", response_model=AIActionOut)
async def create_action(
    data: AIActionCreate,
    # Members may propose work. Creating an action can reach the execution
    # engine when autonomy is enabled, so a read-only viewer must not.
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> AIActionOut:
    return await ActionService(db).create(
        auth.organization_id, data, user_id=auth.user.id, organization=auth.organization
    )


@router.get("/actions/{action_id}", response_model=AIActionOut)
async def get_action(
    action_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> AIActionOut:
    row = await ActionService(db).get(auth.organization_id, action_id)
    return AIActionOut.model_validate(row)


@router.post("/actions/{action_id}/approve", response_model=AIActionOut)
async def approve_action(
    action_id: UUID,
    data: ActionDecision | None = None,
    auth: AuthContext = Depends(require_permission(Permission.action_approve)),
    db: AsyncSession = Depends(get_db),
) -> AIActionOut:
    return await ActionService(db).approve(auth.organization_id, action_id, auth.user.id, data or ActionDecision())


@router.post("/actions/{action_id}/reject", response_model=AIActionOut)
async def reject_action(
    action_id: UUID,
    data: ActionDecision | None = None,
    auth: AuthContext = Depends(require_permission(Permission.action_approve)),
    db: AsyncSession = Depends(get_db),
) -> AIActionOut:
    return await ActionService(db).reject(auth.organization_id, action_id, auth.user.id, data or ActionDecision())


@router.post("/actions/{action_id}/execute", response_model=AIActionOut)
async def execute_action(
    action_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.action_execute)),
    db: AsyncSession = Depends(get_db),
) -> AIActionOut:
    return await ActionService(db).execute(auth.organization_id, action_id, auth.user.id)


@router.post("/actions/{action_id}/cancel", response_model=AIActionOut)
async def cancel_action(
    action_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.action_approve)),
    db: AsyncSession = Depends(get_db),
) -> AIActionOut:
    return await ActionService(db).cancel(auth.organization_id, action_id, auth.user.id)


@router.post("/actions/{action_id}/rollback")
async def rollback_action(
    action_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.action_execute)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await ActionService(db).rollback(auth.organization_id, action_id, auth.user.id)


@router.get("/activity", response_model=list[AIActionOut])
async def activity(
    client_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[AIActionOut]:
    return await ActionService(db).list(auth.organization_id, client_id=client_id, limit=150)


@router.post("/decision-loop", response_model=DecisionLoopResult, dependencies=[Depends(campaign_execution_limit)])
async def decision_loop(
    data: DecisionLoopRequest,
    auth: AuthContext = Depends(require_permission(Permission.autonomous_execution)),
    db: AsyncSession = Depends(get_db),
) -> DecisionLoopResult:
    return await OptimizationService(db).run_decision_loop(auth.organization, data, user_id=auth.user.id)


@router.post("/campaigns/propose", response_model=AIActionOut, dependencies=[Depends(campaign_execution_limit)])
async def propose_campaign(
    data: CampaignProposeRequest,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> AIActionOut:
    return await ActionService(db).create(
        auth.organization_id,
        AIActionCreate(
            action_type=AIActionType.create_campaign,
            client_id=data.client_id,
            agent="AdsAgent",
            platform=data.platform,
            description=f"Propose campaign '{data.name}' ({data.objective})",
            reason=data.reason,
            evidence=[{"daily_budget": str(data.daily_budget)}],
            expected_impact="Campaign created only after approval + platform confirmation",
            estimated_cost=data.daily_budget,
            priority=Priority.high,
            risk_level=RiskLevel.high,
            payload={"name": data.name, "objective": data.objective, "daily_budget": str(data.daily_budget)},
        ),
        user_id=auth.user.id,
    )


@router.post("/content/schedule", response_model=AIActionOut)
async def schedule_content(
    data: ScheduleContentRequest,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> AIActionOut:
    return await ActionService(db).create(
        auth.organization_id,
        AIActionCreate(
            action_type=AIActionType.schedule_content,
            client_id=data.client_id,
            agent="SocialMediaAgent",
            platform=data.platform,
            description=data.description,
            reason="Content schedule request",
            evidence=[],
            payload={"scheduled_for": data.scheduled_for.isoformat(), "content": data.content},
            priority=Priority.medium,
        ),
        user_id=auth.user.id,
    )


@router.post("/content/publish", response_model=AIActionOut, dependencies=[Depends(campaign_execution_limit)])
async def publish_content(
    data: PublishContentRequest,
    auth: AuthContext = Depends(require_permission(Permission.campaign_publish)),
    db: AsyncSession = Depends(get_db),
) -> AIActionOut:
    return await ActionService(db).create(
        auth.organization_id,
        AIActionCreate(
            action_type=AIActionType.publish_content,
            client_id=data.client_id,
            agent="SocialMediaAgent",
            platform=data.platform,
            description=data.description,
            reason="Content publish request",
            evidence=[],
            payload={"content": data.content},
            priority=Priority.high,
            risk_level=RiskLevel.high,
        ),
        user_id=auth.user.id,
    )


# Creative / image / video under autopilot namespace + mirrored paths via creative router
@router.post("/creative/generate", response_model=list[CreativeAssetOut], dependencies=[Depends(media_limit)])
async def creative_generate(
    data: CreativeGenerateRequest,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> list[CreativeAssetOut]:
    return await CreativeService(db).generate_concepts(auth.organization, data, user_id=auth.user.id)


@router.get("/creative/assets", response_model=list[CreativeAssetOut])
async def creative_assets(
    client_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[CreativeAssetOut]:
    return await CreativeService(db).list_assets(auth.organization_id, client_id)


@router.post("/image/generate", dependencies=[Depends(media_limit)])
async def image_generate(
    data: ImageGenerateRequest,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await CreativeService(db).generate_image(auth.organization, data, user_id=auth.user.id)


@router.post("/video/generate", dependencies=[Depends(media_limit)])
async def video_generate(
    data: VideoGenerateRequest,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await CreativeService(db).generate_video(auth.organization, data, user_id=auth.user.id)


@router.get("/video/{job_id}")
async def video_status(
    job_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await CreativeService(db).get_video_job(auth.organization_id, job_id)


@router.get("/optimization/rules", response_model=list[OptimizationRuleOut])
async def list_rules(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[OptimizationRuleOut]:
    return await OptimizationService(db).list_rules(auth.organization_id)


@router.post("/optimization/rules", response_model=OptimizationRuleOut)
async def create_rule(
    data: OptimizationRuleIn,
    auth: AuthContext = Depends(require_permission(Permission.autonomy_manage)),
    db: AsyncSession = Depends(get_db),
) -> OptimizationRuleOut:
    return await OptimizationService(db).create_rule(auth.organization_id, data)


@router.get("/optimization/events", response_model=list[OptimizationEventOut])
async def list_events(
    client_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[OptimizationEventOut]:
    return await OptimizationService(db).list_events(auth.organization_id, client_id)


@router.post("/optimization/analyze", response_model=DecisionLoopResult, dependencies=[Depends(ai_limit)])
async def analyze(
    client_id: UUID = Query(...),
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> DecisionLoopResult:
    return await OptimizationService(db).analyze(auth.organization, client_id, user_id=auth.user.id)


@router.get("/campaigns/health", response_model=list[CampaignHealthOut])
async def campaign_health(
    client_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[CampaignHealthOut]:
    return await OptimizationService(db).list_health(auth.organization_id, client_id)


@router.get("/campaigns/health/summary", dependencies=[Depends(ai_limit)])
async def campaign_health_summary(
    client_id: UUID = Query(...),
    # Reads like a report but calls the AI provider, so it is billed work and
    # gated with the other spending endpoints rather than with plain reads.
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Written explanation of the health scores from `/campaigns/health`.

    The scores themselves stay arithmetic; this only narrates them, and reports
    `narrative_available: false` rather than inventing prose if the AI provider
    is unreachable.
    """
    return await OptimizationService(db).health_narrative(auth.organization, client_id)


@router.post("/jobs/process", dependencies=[Depends(campaign_execution_limit)])
async def process_jobs(
    auth: AuthContext = Depends(require_permission(Permission.campaign_publish)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Ask for this organization's due scheduled posts to be picked up.

    The normal path is: enqueue here, the worker executes. Only a development
    setup with inline execution runs the work in the request, and even then it
    is restricted to the caller's own organization — the queue is shared by
    every tenant, so draining it from a user request would let one customer
    execute another customer's work with that customer's credentials.
    """
    from app.jobs.handlers import enqueue_publish_due, process_organization_jobs

    if not app_settings().should_run_jobs_inline:
        job = await enqueue_publish_due(db, auth.organization_id)
        return {
            "queued": True,
            "job_id": str(job.id),
            "status": job.status.value.upper(),
            "poll_url": f"/api/v1/jobs/{job.id}",
            "processed": 0,
            "ids": [],
            "message": "Queued for the background worker.",
        }

    processed = await process_organization_jobs(db, auth.organization_id)
    return {
        "queued": True,
        "processed": len(processed),
        "ids": [str(j.id) for j in processed],
    }


@router.post("/cycle", response_model=AutopilotCycleResult, dependencies=[Depends(campaign_execution_limit)])
async def run_autopilot_cycle(
    data: AutopilotCycleRequest,
    auth: AuthContext = Depends(require_permission(Permission.autonomous_execution)),
    db: AsyncSession = Depends(get_db),
) -> AutopilotCycleResult:
    return await AutopilotOrchestratorService(db).run_cycle(
        auth.organization,
        client_id=data.client_id,
        run_id=data.run_id,
        user_id=auth.user.id,
        max_iterations=data.max_iterations,
    )


@router.get("/capabilities")
async def provider_capabilities(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
    client_id: UUID | None = Query(default=None),
) -> dict:
    from app.core.config import get_settings
    from app.integrations.persistence import get_integration_row
    from app.publishing.capabilities import (
        google_ads_capabilities,
        instagram_publish_capabilities,
        meta_ads_capabilities,
    )

    settings = get_settings()
    meta_row = await get_integration_row(db, organization_id=auth.organization_id, provider="meta", client_id=client_id)
    google_row = await get_integration_row(
        db, organization_id=auth.organization_id, provider="google_ads", client_id=client_id
    )
    ig_row = await get_integration_row(
        db, organization_id=auth.organization_id, provider="instagram", client_id=client_id
    )
    meta_connected = bool(meta_row and meta_row.secret_ref and meta_row.status == "connected")
    google_connected = bool(google_row and google_row.secret_ref and google_row.status == "connected")
    ig_connected = bool(ig_row and ig_row.secret_ref and ig_row.status == "connected")
    return {
        "providers": [
            meta_ads_capabilities(
                connected=meta_connected,
                credentials_configured=bool(settings.meta_app_id and settings.meta_app_secret),
            ).as_dict(),
            google_ads_capabilities(
                connected=google_connected,
                credentials_configured=bool(
                    settings.google_client_id and settings.google_client_secret and settings.google_ads_developer_token
                ),
            ).as_dict(),
            instagram_publish_capabilities(connected=ig_connected).as_dict(),
        ],
        "demo_mode": auth.demo_mode,
    }


@router.post("/campaigns/build", response_model=CampaignBuildResult, dependencies=[Depends(campaign_execution_limit)])
async def build_campaign(
    data: CampaignBuildRequest,
    auth: AuthContext = Depends(require_permission(Permission.campaign_publish)),
    db: AsyncSession = Depends(get_db),
) -> CampaignBuildResult:
    return await CampaignBuildService(db).build_campaign(auth.organization, data, user_id=auth.user.id)


@router.post("/run", response_model=AutopilotRunOut, dependencies=[Depends(campaign_execution_limit)])
async def run_marketing_autopilot(
    data: AutopilotRunRequest,
    auth: AuthContext = Depends(require_permission(Permission.autonomous_execution)),
    db: AsyncSession = Depends(get_db),
) -> AutopilotRunOut:
    return await CampaignBuildService(db).run_autopilot(auth.organization, data, user_id=auth.user.id)


@router.get("/runs", response_model=list[AutopilotRunOut])
async def list_runs(
    client_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[AutopilotRunOut]:
    return await CampaignBuildService(db).list_runs(auth.organization_id, client_id)


@router.get("/runs/{run_id}", response_model=AutopilotRunOut)
async def get_run(
    run_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> AutopilotRunOut:
    try:
        return await CampaignBuildService(db).get_run(auth.organization_id, run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="RUN_NOT_FOUND") from None


@router.post("/creative/variations", dependencies=[Depends(media_limit)])
async def creative_variations(
    data: CreativeVariationsRequest,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await CampaignBuildService(db).generate_variations(auth.organization, data, user_id=auth.user.id)


@router.get("/creative/library", response_model=list[CreativeAssetOut])
async def creative_library(
    client_id: UUID | None = Query(default=None),
    asset_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[CreativeAssetOut]:
    return await CampaignBuildService(db).creative_library(
        auth.organization_id, client_id=client_id, asset_type=asset_type, status=status
    )
