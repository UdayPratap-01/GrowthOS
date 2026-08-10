"""
P2-A campaign generation: orchestration, persistence, approval.

What this service is responsible for
------------------------------------
Turning one "Generate Campaign" request into a reviewable package: strategy,
brief, copy concepts, visual specs, real media jobs, variations, and a campaign →
ad set → ad structure. It owns the order those happen in, what gets persisted at
each step, and what the run reports while it is happening.

Three decisions shape the whole file.

**Nothing here publishes.** No adapter is called, no budget reaches a platform,
and `Campaign.status` stays "draft" while `review_status` moves through the
internal lifecycle. `external_id` is only ever written by a real integration
confirming a real external campaign, which does not exist in this phase.

**Progress is observed, never estimated.** Each stage's `completed`/`total` are
counts of rows that exist: concepts persisted, image jobs finished. A run reaches
READY_FOR_REVIEW when the structure is built and every media job it started has
reached a terminal state — not after a timer, and not optimistically.

**A missing provider is a reported outcome, not a failure.** If no image provider
is configured, the images stage is NOT_CONFIGURED, the rest of the campaign still
generates, and the reviewer sees exactly which parts are absent and why. The one
thing that never happens is a placeholder file standing in for a real asset.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.ai.agents.campaign_builder_agent import CampaignBuilderRequest
from app.ai.agents.campaign_strategy_agent import CampaignStrategyRequest
from app.ai.agents.copy_agent import CopyRequest
from app.ai.agents.creative_brief_agent import CreativeBriefRequest
from app.ai.agents.creative_concept_agent import CreativeConceptRequest
from app.ai.agents.variation_agent import VariationRequest as AgentVariationRequest
from app.ai.orchestrator import get_orchestrator
from app.ai.providers.base import AIGenerationError
from app.campaigns import registry
from app.campaigns.context import CampaignContextBuilder
from app.campaigns.errors import (
    CampaignStateConflict,
    InvalidCampaignRequest,
    UsageLimitReached,
)
from app.core.config import get_settings
from app.core.mode import effective_demo_mode
from app.generation import get_image_provider, get_video_provider
from app.models.automation import CreativeAsset, ImageJob, VideoJob
from app.models.creative import (
    CampaignBrief,
    CampaignGenerationRun,
    CreativeConcept,
    CreativeVariation,
)
from app.models.ai_ops import Integration
from app.models.enums import DataSource, JobStatus
from app.models.marketing import Ad, AdSet, Campaign
from app.schemas.campaign_generation import (
    AdOut,
    AdSetOut,
    AspectRatioOptionOut,
    CampaignApprovalOut,
    CampaignBriefOut,
    CampaignGenerateRequest,
    CampaignGeneratorOptionsOut,
    CampaignGenerationRunOut,
    CampaignPackageOut,
    CampaignStrategy,
    CampaignSummaryOut,
    ConceptAssetOut,
    ConceptRegenerateRequest,
    CreativeConceptOut,
    CreativeVariationOut,
    GenerationLimitsOut,
    MediaProviderStatusOut,
    ObjectiveOptionOut,
    PlatformAvailabilityOut,
    VariationGenerateRequest,
)
from app.services.billing_service import BillingService, QuotaExceeded, SubscriptionInactive
from app.services.client_service import ClientService
from app.services.media_generation_service import MediaGenerationService
from app.services.usage_service import Metric, meter
from app.storage import key_belongs_to_organization

logger = logging.getLogger("growthos.campaign_generation")

# Run lifecycle. Deliberately not `Campaign.status`, which describes platform
# delivery state and must stay "draft" for anything unpublished.
RUN_QUEUED = "QUEUED"
RUN_GENERATING = "GENERATING"
RUN_READY = "READY_FOR_REVIEW"
RUN_FAILED = "FAILED"
TERMINAL_RUN_STATUSES = {RUN_READY, RUN_FAILED, "CANCELLED"}

# Campaign review lifecycle. No PUBLISHED member: nothing in this phase can
# confirm an external campaign, so the state has no honest value to hold.
REVIEW_DRAFT = "DRAFT"
REVIEW_GENERATING = "GENERATING"
REVIEW_READY = "READY_FOR_REVIEW"
REVIEW_APPROVED = "APPROVED"
REVIEW_REJECTED = "REJECTED"
REVIEW_READY_TO_PUBLISH = "READY_TO_PUBLISH"

STAGE_PENDING = "PENDING"
STAGE_RUNNING = "RUNNING"
STAGE_COMPLETED = "COMPLETED"
STAGE_FAILED = "FAILED"
STAGE_SKIPPED = "SKIPPED"
STAGE_NOT_CONFIGURED = "NOT_CONFIGURED"

#: Fixed order, so the UI can render a stable checklist from the first poll
#: rather than having stages appear as they start.
STAGE_PLAN: tuple[tuple[str, str], ...] = (
    ("context", "Client context"),
    ("strategy", "Campaign strategy"),
    ("brief", "Creative brief"),
    ("copy", "Ad copy"),
    ("concepts", "Creative concepts"),
    ("images", "Images"),
    ("videos", "Videos"),
    ("variations", "Creative variations"),
    ("structure", "Campaign structure"),
)

CAMPAIGN_GENERATE_JOB = "campaign.generate"
CAMPAIGN_RECONCILE_JOB = "campaign.reconcile"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


class CampaignGenerationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Generator options
    # ------------------------------------------------------------------

    async def options(self, organization) -> CampaignGeneratorOptionsOut:
        """
        Everything the generator form needs, with connection state resolved from
        this organization's integrations.

        Connection status is read per request rather than baked into the platform
        registry: the registry knows what the generator supports, only the
        database knows what this customer has connected.
        """
        connected = await self._connected_providers(organization.id)
        platforms = []
        for spec in registry.list_platforms():
            status = connected.get(spec.integration_provider or "", "not_connected")
            platforms.append(
                PlatformAvailabilityOut(
                    key=spec.key,
                    label=spec.label,
                    aspect_ratios=list(spec.aspect_ratios),
                    default_image_ratio=spec.default_image_ratio,
                    default_video_ratio=spec.default_video_ratio,
                    placements=list(spec.placements),
                    supports_video=spec.supports_video,
                    headline_max_chars=spec.headline_max_chars,
                    primary_text_max_chars=spec.primary_text_max_chars,
                    connected=status == "connected",
                    connection_status=status,
                    # Always False in P2-A regardless of connection state.
                    # Connected means "we can read from it", not "we can spend".
                    publishing_supported=False,
                    notes=spec.notes,
                )
            )

        limits = registry.generation_limits()
        return CampaignGeneratorOptionsOut(
            platforms=platforms,
            objectives=[
                ObjectiveOptionOut(
                    key=spec.key,
                    label=spec.label,
                    description=spec.description,
                    optimization=spec.optimization,
                    success_metrics=list(spec.success_metrics),
                )
                for spec in registry.list_objectives()
            ],
            aspect_ratios=[
                AspectRatioOptionOut(
                    key=spec.key,
                    label=spec.label,
                    width=spec.width,
                    height=spec.height,
                    usage=spec.usage,
                    orientation=spec.orientation,
                )
                for spec in registry.list_aspect_ratios()
            ],
            limits=GenerationLimitsOut(
                max_concepts=limits.max_concepts,
                max_images=limits.max_images,
                max_videos=limits.max_videos,
                max_variations=limits.max_variations,
            ),
            media=self.media_status(organization),
        )

    def media_status(self, organization) -> MediaProviderStatusOut:
        settings = get_settings()
        image = get_image_provider()
        video = get_video_provider()
        notes = []
        if not image.configured():
            notes.append("Image generation is not configured; image stages report NOT_CONFIGURED.")
        if not video.configured():
            notes.append("Video generation is not configured; video stages report NOT_CONFIGURED.")
        return MediaProviderStatusOut(
            image_provider=image.name,
            image_configured=image.configured(),
            video_provider=video.name,
            video_configured=video.configured(),
            storage_backend=settings.storage_backend,
            demo_mode=effective_demo_mode(organization),
            message=" ".join(notes) if notes else "Media providers ready.",
        )

    async def _connected_providers(self, organization_id: UUID) -> dict[str, str]:
        rows = await self.db.execute(
            select(Integration.provider, Integration.status).where(
                Integration.organization_id == organization_id
            )
        )
        out: dict[str, str] = {}
        for provider, status in rows.all():
            value = getattr(status, "value", str(status))
            # Any connected account for a provider makes it connected; several
            # clients may share one integration.
            if out.get(provider) != "connected":
                out[str(provider)] = value
        return out

    # ------------------------------------------------------------------
    # Starting a run
    # ------------------------------------------------------------------

    async def start(
        self, organization, user_id: UUID | None, request: CampaignGenerateRequest
    ) -> CampaignGenerationRunOut:
        """
        Validate, record the run, and hand the work to the queue.

        Every guardrail is applied here, before a single provider call: client
        ownership, platform and objective validity, plan quotas, and the
        server-side quantity ceilings. The frontend's own limits are a courtesy;
        these are the control that prevents a crafted request from starting
        fifty video generations.
        """
        # Ownership: raises 404 for a client in another organization, which is
        # also the correct answer — it does not exist as far as this caller goes.
        await ClientService(self.db).get_client(organization.id, request.client_id)

        try:
            platform = registry.platform(request.platform)
            objective = registry.objective(request.objective)
        except registry.UnknownCampaignOption as exc:
            raise InvalidCampaignRequest(str(exc).split(": ", 1)[-1]) from exc

        limits = registry.generation_limits()
        concept_quantity = limits.clamp_concepts(request.concept_quantity)
        image_quantity = limits.clamp_images(request.image_quantity)
        video_quantity = limits.clamp_videos(request.video_quantity)
        variation_quantity = limits.clamp_variations(request.variation_quantity)

        if video_quantity and not platform.supports_video:
            raise InvalidCampaignRequest(
                f"{platform.label} does not support video creative in this configuration."
            )

        ratios = registry.resolve_aspect_ratios(platform.key, request.aspect_ratios)

        # Plan enforcement before any spend. Requested media counts are checked
        # as amounts, so a request for ten images against three remaining is
        # refused now rather than half-completed later.
        await self._require_quota(organization.id, Metric.CAMPAIGN_GENERATION, 1)
        if image_quantity:
            await self._require_quota(organization.id, Metric.IMAGE_GENERATION, image_quantity)
        if video_quantity:
            await self._require_quota(organization.id, Metric.VIDEO_GENERATION, video_quantity)

        idempotency_key = request.idempotency_key
        if idempotency_key:
            existing = await self.db.scalar(
                select(CampaignGenerationRun).where(
                    CampaignGenerationRun.organization_id == organization.id,
                    CampaignGenerationRun.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                # A retry or a double-click. Return the original run instead of
                # starting a second one that would bill twice.
                return await self._run_out(existing)

        run = CampaignGenerationRun(
            organization_id=organization.id,
            client_id=request.client_id,
            requested_by=user_id,
            status=RUN_QUEUED,
            platform=platform.key,
            objective=objective.key,
            request={
                **request.model_dump(mode="json", exclude={"idempotency_key"}),
                "aspect_ratios": ratios,
                "concept_quantity": concept_quantity,
                "image_quantity": image_quantity,
                "video_quantity": video_quantity,
                "variation_quantity": variation_quantity,
            },
            stages=_initial_stages(
                concept_quantity=concept_quantity,
                image_quantity=image_quantity,
                video_quantity=video_quantity,
                variation_quantity=variation_quantity,
            ),
            idempotency_key=idempotency_key,
            concept_quantity=concept_quantity,
            image_quantity=image_quantity,
            video_quantity=video_quantity,
            variation_quantity=variation_quantity,
            demo_mode=effective_demo_mode(organization),
        )
        self.db.add(run)
        try:
            await self.db.flush()
        except IntegrityError:
            # Two concurrent requests with the same idempotency key raced. The
            # loser reads the winner's run.
            await self.db.rollback()
            existing = await self.db.scalar(
                select(CampaignGenerationRun).where(
                    CampaignGenerationRun.organization_id == organization.id,
                    CampaignGenerationRun.idempotency_key == idempotency_key,
                )
            )
            if existing is None:
                raise
            return await self._run_out(existing)

        await self._dispatch_run(organization, run)
        return await self._run_out(run)

    async def _dispatch_run(self, organization, run: CampaignGenerationRun) -> None:
        """
        Enqueue the generation, or run it here in development.

        Production always enqueues: a campaign involves several model calls and
        can fan out into minutes of media generation, so holding the HTTP request
        would lose the work on any restart. Inline execution exists only where
        `should_run_jobs_inline` is set — development and tests — and follows the
        same pattern as the media pipeline so both paths exercise the same code.
        """
        if get_settings().should_run_jobs_inline:
            await self.execute(organization, run)
            return

        from app.jobs.queue import JobQueue

        job = await JobQueue(self.db).enqueue(
            job_type=CAMPAIGN_GENERATE_JOB,
            payload={"run_id": str(run.id)},
            organization_id=organization.id,
            dedupe_key=f"campaign-generate:{run.id}",
        )
        run.background_job_id = job.id
        await self.db.flush()

    async def _require_quota(self, organization_id: UUID, metric: str, amount: int) -> None:
        try:
            await BillingService(self.db).require_quota(organization_id, metric, amount=amount)
        except QuotaExceeded as exc:
            raise UsageLimitReached(str(exc), details={"metric": exc.metric}) from exc
        except SubscriptionInactive as exc:
            raise UsageLimitReached(str(exc), details={"metric": metric}) from exc

    # ------------------------------------------------------------------
    # The pipeline
    # ------------------------------------------------------------------

    async def execute(self, organization, run: CampaignGenerationRun) -> None:
        """
        Run every generation stage in order.

        Stages are attempted in sequence because each one consumes the previous
        one's output. A failure records the stage that failed and the run stops:
        continuing after a failed strategy would produce copy for a campaign
        whose reasoning is unknown.
        """
        if run.status in TERMINAL_RUN_STATUSES:
            return
        run.status = RUN_GENERATING
        run.started_at = run.started_at or _now()
        await self.db.flush()

        try:
            await self._execute_stages(organization, run)
        except AIGenerationError as exc:
            await self._fail_run(
                run,
                code="AI_GENERATION_FAILED",
                message="The AI provider could not complete this stage.",
                detail=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 — the run must record every failure
            logger.exception(
                "Campaign generation failed",
                extra={
                    "event": "campaign_generation.failed",
                    "run_id": str(run.id),
                    "org": str(run.organization_id),
                },
            )
            await self._fail_run(
                run,
                code="CAMPAIGN_GENERATION_FAILED",
                message="Campaign generation did not complete.",
                detail=type(exc).__name__,
            )

    async def _execute_stages(self, organization, run: CampaignGenerationRun) -> None:
        settings_request = run.request or {}
        platform = registry.platform(run.platform)
        objective = registry.objective(run.objective)
        ratios = registry.resolve_aspect_ratios(platform.key, settings_request.get("aspect_ratios"))
        currency = str(settings_request.get("currency") or "USD")
        duration_days = int(settings_request.get("duration_days") or 30)

        # -- context ------------------------------------------------------
        self._stage(run, "context", STAGE_RUNNING)
        campaign_context = await CampaignContextBuilder(self.db).build(organization, run.client_id)
        context = campaign_context.client_context
        gaps = campaign_context.data_limitations
        run.data_limitations = list(gaps)
        self._stage(
            run,
            "context",
            STAGE_COMPLETED,
            detail=(
                f"{len(gaps)} data limitation(s) recorded."
                if gaps
                else "Client context assembled from stored data."
            ),
        )
        await self.db.flush()

        orchestrator = get_orchestrator()
        evidence_rows = self._historical_evidence(context)

        # -- strategy -----------------------------------------------------
        self._stage(run, "strategy", STAGE_RUNNING)
        strategy = await orchestrator.campaign_strategy(
            context,
            CampaignStrategyRequest(
                platform=platform.key,
                platform_label=platform.label,
                objective=objective.key,
                objective_label=objective.label,
                objective_description=objective.description,
                optimization=objective.optimization,
                suggested_success_metrics=list(objective.success_metrics),
                offer=settings_request.get("offer"),
                audience=settings_request.get("audience"),
                tone=settings_request.get("tone"),
                total_budget=_float(settings_request.get("total_budget")),
                daily_budget=_float(settings_request.get("daily_budget")),
                monthly_budget=_float(settings_request.get("monthly_budget")),
                currency=currency,
                duration_days=duration_days,
                placements=list(platform.placements),
                historical_evidence=evidence_rows,
                known_data_gaps=gaps,
            ),
        )
        gaps = _merge_limitations(gaps, strategy.data_limitations)
        self._stage(run, "strategy", STAGE_COMPLETED, detail=strategy.core_message[:200])
        await meter(
            self.db,
            organization_id=organization.id,
            metric=Metric.STRATEGY_GENERATION,
            idempotency_key=f"campaign_strategy:{run.id}",
            client_id=run.client_id,
            details={"run_id": str(run.id)},
        )
        await self.db.flush()

        # -- brief --------------------------------------------------------
        self._stage(run, "brief", STAGE_RUNNING)
        draft = await orchestrator.creative_brief(
            context,
            CreativeBriefRequest(
                platform=platform.key,
                platform_label=platform.label,
                objective=objective.key,
                objective_label=objective.label,
                offer=settings_request.get("offer"),
                audience=settings_request.get("audience"),
                tone=settings_request.get("tone"),
                cta=settings_request.get("cta"),
                requested_name=settings_request.get("campaign_name"),
                strategy=strategy.model_dump(),
                known_data_gaps=gaps,
            ),
        )
        gaps = _merge_limitations(gaps, draft.data_limitations)

        campaign_name = (
            settings_request.get("campaign_name") or draft.campaign_name or "AI campaign"
        )[:200]
        brief = CampaignBrief(
            organization_id=organization.id,
            client_id=run.client_id,
            campaign_name=campaign_name,
            platform=platform.key,
            objective=objective.key,
            offer=draft.offer or settings_request.get("offer"),
            audience=draft.audience or settings_request.get("audience"),
            pain_points=list(draft.pain_points),
            value_proposition=draft.value_proposition,
            messaging_angle=draft.messaging_angle,
            tone=draft.tone or settings_request.get("tone"),
            brand_constraints=list(draft.brand_constraints),
            # Budgets come from the request, never from the model: a
            # model-chosen budget would be an invented spend commitment.
            total_budget=_decimal(settings_request.get("total_budget")),
            daily_budget=_decimal(settings_request.get("daily_budget")),
            monthly_budget=_decimal(settings_request.get("monthly_budget")),
            currency=currency,
            success_metrics=list(draft.success_metrics or objective.success_metrics),
            creative_direction=draft.creative_direction,
            cta=draft.cta or settings_request.get("cta"),
            data_limitations=list(gaps),
            strategy=strategy.model_dump(),
            client_context_snapshot=context.model_dump(mode="json"),
            data_source=DataSource.demo.value if run.demo_mode else DataSource.live.value,
        )
        self.db.add(brief)
        await self.db.flush()
        run.brief_id = brief.id
        self._stage(run, "brief", STAGE_COMPLETED, detail=brief.campaign_name)

        # The campaign row exists from here on so concepts, media jobs and assets
        # can all reference it while generation is still in progress.
        campaign = Campaign(
            organization_id=organization.id,
            client_id=run.client_id,
            name=campaign_name,
            platform=platform.key,
            objective=objective.key,
            # Platform delivery state. Stays "draft" for the whole of P2-A —
            # nothing here can make a campaign live.
            status="draft",
            review_status=REVIEW_GENERATING,
            brief_id=brief.id,
            audience=brief.audience,
            total_budget=brief.total_budget,
            daily_budget=brief.daily_budget,
            monthly_budget=brief.monthly_budget,
            currency=currency,
            generated_by_ai=True,
            data_source=DataSource.demo if run.demo_mode else DataSource.live,
        )
        self.db.add(campaign)
        await self.db.flush()
        run.campaign_id = campaign.id
        await self.db.flush()

        # -- copy ---------------------------------------------------------
        self._stage(run, "copy", STAGE_RUNNING)
        copy_pack = await orchestrator.campaign_copy(
            context,
            CopyRequest(
                count=run.concept_quantity,
                platform=platform.key,
                platform_label=platform.label,
                objective=objective.key,
                objective_label=objective.label,
                headline_max_chars=platform.headline_max_chars,
                primary_text_max_chars=platform.primary_text_max_chars,
                description_max_chars=platform.description_max_chars,
                tone=brief.tone,
                cta=brief.cta,
                brief=_brief_payload(brief),
                strategy=strategy.model_dump(),
                known_data_gaps=gaps,
            ),
        )
        gaps = _merge_limitations(gaps, copy_pack.data_limitations)
        concepts_copy = list(copy_pack.concepts)[: run.concept_quantity]
        if not concepts_copy:
            raise InvalidCampaignRequest("The copy agent returned no concepts.")
        self._stage(
            run,
            "copy",
            STAGE_COMPLETED,
            completed=len(concepts_copy),
            total=run.concept_quantity,
            detail=f"{len({c.angle for c in concepts_copy})} distinct angle(s).",
        )
        await meter(
            self.db,
            organization_id=organization.id,
            metric=Metric.COPY_GENERATION,
            idempotency_key=f"campaign_copy:{run.id}",
            quantity=len(concepts_copy),
            client_id=run.client_id,
            details={"run_id": str(run.id)},
        )
        await self.db.flush()

        # -- creative concepts --------------------------------------------
        self._stage(run, "concepts", STAGE_RUNNING)
        needs_video = bool(run.video_quantity)
        concept_pack = await orchestrator.creative_concepts(
            context,
            CreativeConceptRequest(
                platform=platform.key,
                platform_label=platform.label,
                objective=objective.key,
                aspect_ratios=ratios,
                aspect_ratio_guidance=[_ratio_payload(r) for r in ratios],
                needs_video=needs_video,
                brief=_brief_payload(brief),
                strategy=strategy.model_dump(),
                copy_concepts=[c.model_dump() for c in concepts_copy],
                known_data_gaps=gaps,
            ),
        )
        gaps = _merge_limitations(gaps, concept_pack.data_limitations)
        specs = {spec.concept_id: spec for spec in concept_pack.specs}

        concepts: list[CreativeConcept] = []
        for copy_concept in concepts_copy:
            spec = specs.get(copy_concept.concept_id)
            visual = spec.visual_direction.model_dump() if spec else {}
            if spec and spec.creative_concept:
                visual = {**visual, "creative_concept": spec.creative_concept}
            concept = CreativeConcept(
                organization_id=organization.id,
                client_id=run.client_id,
                brief_id=brief.id,
                campaign_id=campaign.id,
                run_id=run.id,
                reference=copy_concept.concept_id[:16],
                angle=copy_concept.angle,
                hook=copy_concept.hook,
                primary_text=copy_concept.primary_text,
                headline=copy_concept.headline,
                description=copy_concept.description,
                cta=copy_concept.cta or brief.cta,
                tone=copy_concept.tone or brief.tone,
                audience=copy_concept.audience or brief.audience,
                objective=objective.key,
                platform=platform.key,
                visual_direction=visual,
                image_prompt=spec.image_prompt if spec else None,
                video_prompt=(spec.video_prompt if spec else None) if needs_video else None,
                negative_constraints=_negative_constraints(spec),
                aspect_ratios=list(spec.aspect_ratios) if spec and spec.aspect_ratios else ratios,
                status="READY",
                data_limitations=list(gaps),
                meta={"hypothesis": copy_concept.hypothesis} if copy_concept.hypothesis else {},
                data_source=DataSource.demo.value if run.demo_mode else DataSource.live.value,
            )
            self.db.add(concept)
            concepts.append(concept)
        await self.db.flush()
        missing_visuals = [c.reference for c in concepts if not c.image_prompt]
        self._stage(
            run,
            "concepts",
            STAGE_COMPLETED,
            completed=len(concepts),
            total=run.concept_quantity,
            detail=(
                f"No visual specification returned for concept(s) {', '.join(missing_visuals)}."
                if missing_visuals
                else None
            ),
        )
        await self.db.flush()

        # -- media --------------------------------------------------------
        await self._start_media(organization, run, campaign, concepts)

        # -- variations ---------------------------------------------------
        if run.variation_quantity and concepts:
            self._stage(run, "variations", STAGE_RUNNING)
            created = await self._generate_variations(
                organization,
                run=run,
                campaign=campaign,
                brief=brief,
                context=context,
                parent=concepts[0],
                count=run.variation_quantity,
                allowed_axes=[],
                platform=platform,
                objective=objective,
                gaps=gaps,
                generate_media=False,
            )
            self._stage(
                run,
                "variations",
                STAGE_COMPLETED,
                completed=len(created),
                total=run.variation_quantity,
                detail=", ".join(f"{v.reference}: {v.axis}" for v in created) or None,
            )
        else:
            self._stage(run, "variations", STAGE_SKIPPED, detail="No variations requested.")
        await self.db.flush()

        # -- structure ----------------------------------------------------
        self._stage(run, "structure", STAGE_RUNNING)
        await self._build_structure(
            organization,
            run=run,
            campaign=campaign,
            brief=brief,
            context=context,
            concepts=concepts,
            strategy=strategy,
            platform=platform,
            objective=objective,
            duration_days=duration_days,
            currency=currency,
            gaps=gaps,
        )

        run.data_limitations = list(gaps)
        run.result = {
            "campaign_id": str(campaign.id),
            "brief_id": str(brief.id),
            "concept_ids": [str(c.id) for c in concepts],
            "concept_references": [c.reference for c in concepts],
        }
        flag_modified(run, "result")
        await self.db.flush()

        # Media may still be running. `reconcile` decides whether the run is
        # already reviewable or has to wait for the outstanding jobs.
        await self.reconcile(run)

    # -- media ------------------------------------------------------------

    async def _start_media(
        self,
        organization,
        run: CampaignGenerationRun,
        campaign: Campaign,
        concepts: list[CreativeConcept],
    ) -> None:
        """
        Enqueue the real image and video jobs for this run.

        Quantities are spread round-robin across concepts so three images over
        three concepts gives each concept one, rather than three of the first.
        Nothing is generated here: `MediaGenerationService` owns the provider
        call, and in production it happens in a worker.
        """
        media = MediaGenerationService(self.db)

        if not run.image_quantity:
            self._stage(run, "images", STAGE_SKIPPED, detail="No images requested.")
        elif not get_image_provider().configured():
            self._stage(
                run,
                "images",
                STAGE_NOT_CONFIGURED,
                total=run.image_quantity,
                detail=(
                    "No image provider is configured, so no images were generated. "
                    "Concepts include the prompts that would have been used."
                ),
            )
        else:
            self._stage(run, "images", STAGE_RUNNING, total=run.image_quantity)
            enqueued = 0
            for index, concept in _round_robin(concepts, run.image_quantity):
                prompt = _image_prompt(concept)
                if not prompt:
                    continue
                result = await media.enqueue_images(
                    organization,
                    client_id=run.client_id,
                    campaign_id=campaign.id,
                    prompt=prompt,
                    aspect_ratio=(concept.aspect_ratios or ["1:1"])[0],
                    quantity=1,
                    platform=concept.platform,
                    concept_id=concept.id,
                    run_id=run.id,
                    idempotency_key=f"run:{run.id}:image:{index}",
                )
                if result.get("status") != "NOT_CONFIGURED":
                    enqueued += 1
            self._stage(
                run,
                "images",
                STAGE_RUNNING if enqueued else STAGE_FAILED,
                total=run.image_quantity,
                detail=None if enqueued else "No concept carried a usable image prompt.",
            )

        if not run.video_quantity:
            self._stage(run, "videos", STAGE_SKIPPED, detail="No videos requested.")
        elif not get_video_provider().configured():
            self._stage(
                run,
                "videos",
                STAGE_NOT_CONFIGURED,
                total=run.video_quantity,
                detail=(
                    "No video provider is configured, so no videos were generated. "
                    "Concepts include the video prompts that would have been used."
                ),
            )
        else:
            self._stage(run, "videos", STAGE_RUNNING, total=run.video_quantity)
            enqueued = 0
            for index, concept in _round_robin(concepts, run.video_quantity):
                prompt = concept.video_prompt or _image_prompt(concept)
                if not prompt:
                    continue
                ratio = _video_ratio(concept)
                result = await media.enqueue_video(
                    organization,
                    client_id=run.client_id,
                    campaign_id=campaign.id,
                    prompt=prompt,
                    aspect_ratio=ratio,
                    platform=concept.platform,
                    concept_id=concept.id,
                    run_id=run.id,
                    idempotency_key=f"run:{run.id}:video:{index}",
                )
                if result.get("status") != "NOT_CONFIGURED":
                    enqueued += 1
            self._stage(
                run,
                "videos",
                STAGE_RUNNING if enqueued else STAGE_FAILED,
                total=run.video_quantity,
                detail=None if enqueued else "No concept carried a usable video prompt.",
            )
        await self.db.flush()

    # -- variations -------------------------------------------------------

    async def _generate_variations(
        self,
        organization,
        *,
        run: CampaignGenerationRun | None,
        campaign: Campaign | None,
        brief: CampaignBrief | None,
        context,
        parent: CreativeConcept,
        count: int,
        allowed_axes: list[str],
        platform,
        objective,
        gaps: list[str],
        generate_media: bool,
    ) -> list[CreativeVariation]:
        used = await self._used_references(parent)
        pack = await get_orchestrator().creative_variations(
            context,
            AgentVariationRequest(
                count=count,
                platform=platform.key,
                platform_label=platform.label,
                objective=objective.key,
                allowed_axes=list(allowed_axes),
                allowed_aspect_ratios=list(platform.aspect_ratios),
                needs_media=generate_media,
                parent_concept=_concept_payload(parent),
                brief=_brief_payload(brief) if brief else {},
                used_references=used,
                known_data_gaps=gaps,
            ),
        )

        created: list[CreativeVariation] = []
        taken = set(used)
        for spec in pack.variations[:count]:
            reference = spec.reference if spec.reference not in taken else _next_reference(taken)
            taken.add(reference)
            variation = CreativeVariation(
                organization_id=parent.organization_id,
                client_id=parent.client_id,
                parent_concept_id=parent.id,
                campaign_id=campaign.id if campaign else parent.campaign_id,
                run_id=run.id if run else None,
                reference=reference[:16],
                axis=spec.axis,
                hypothesis=spec.hypothesis,
                creative_type=spec.creative_type,
                hook=spec.hook or parent.hook,
                primary_text=spec.primary_text or parent.primary_text,
                headline=spec.headline or parent.headline,
                description=spec.description or parent.description,
                cta=spec.cta or parent.cta,
                tone=spec.tone or parent.tone,
                audience=spec.audience or parent.audience,
                aspect_ratio=spec.aspect_ratio or (parent.aspect_ratios or ["1:1"])[0],
                visual_direction=(
                    spec.visual_direction.model_dump()
                    if spec.visual_direction
                    else dict(parent.visual_direction or {})
                ),
                image_prompt=spec.image_prompt or parent.image_prompt,
                video_prompt=spec.video_prompt or parent.video_prompt,
                negative_constraints=list(spec.negative_constraints)
                or list(parent.negative_constraints or []),
                status="READY",
                data_source=parent.data_source,
            )
            self.db.add(variation)
            created.append(variation)
        await self.db.flush()

        if created:
            await meter(
                self.db,
                organization_id=organization.id,
                metric=Metric.VARIATION_GENERATION,
                quantity=len(created),
                idempotency_key=(
                    f"variations:run:{run.id}"
                    if run is not None
                    else f"variations:concept:{parent.id}:{created[0].id}"
                ),
                client_id=parent.client_id,
                details={"parent_concept_id": str(parent.id)},
            )

        if generate_media:
            await self._start_variation_media(organization, created, run=run)
        return created

    async def _start_variation_media(
        self,
        organization,
        variations: list[CreativeVariation],
        *,
        run: CampaignGenerationRun | None,
    ) -> None:
        """Generate media only for variations whose axis is actually visual."""
        media = MediaGenerationService(self.db)
        for variation in variations:
            if variation.creative_type == "video" and variation.video_prompt:
                await media.enqueue_video(
                    organization,
                    client_id=variation.client_id,
                    campaign_id=variation.campaign_id,
                    prompt=variation.video_prompt,
                    aspect_ratio=variation.aspect_ratio or "9:16",
                    variation_id=variation.id,
                    run_id=run.id if run else None,
                    idempotency_key=f"variation:{variation.id}:video",
                )
            elif variation.creative_type == "image" and variation.image_prompt:
                await media.enqueue_images(
                    organization,
                    client_id=variation.client_id,
                    campaign_id=variation.campaign_id,
                    prompt=_compose_prompt(
                        variation.image_prompt, variation.negative_constraints or []
                    ),
                    aspect_ratio=variation.aspect_ratio or "1:1",
                    quantity=1,
                    variation_id=variation.id,
                    run_id=run.id if run else None,
                    idempotency_key=f"variation:{variation.id}:image",
                )

    async def create_variations(
        self,
        organization,
        *,
        concept_id: UUID,
        request: VariationGenerateRequest,
    ) -> list[CreativeVariationOut]:
        """Vary one existing concept. Used by the "Create Variations" action."""
        concept = await self._concept(organization.id, concept_id)
        limits = registry.generation_limits()
        count = min(request.count, limits.max_variations)
        if count <= 0:
            raise InvalidCampaignRequest("Variation limit for this deployment is zero.")

        await self._require_quota(organization.id, Metric.AI_REQUEST, 1)
        if request.generate_media:
            await self._require_quota(organization.id, Metric.IMAGE_GENERATION, count)

        campaign_context = await CampaignContextBuilder(self.db).build(
            organization, concept.client_id
        )
        brief = await self.db.get(CampaignBrief, concept.brief_id) if concept.brief_id else None
        campaign = await self.db.get(Campaign, concept.campaign_id) if concept.campaign_id else None

        created = await self._generate_variations(
            organization,
            run=None,
            campaign=campaign,
            brief=brief,
            context=campaign_context.client_context,
            parent=concept,
            count=count,
            allowed_axes=list(request.axes),
            platform=registry.platform(concept.platform),
            objective=registry.objective(concept.objective),
            gaps=campaign_context.data_limitations,
            generate_media=request.generate_media,
        )
        return [await self._variation_out(v) for v in created]

    # -- structure --------------------------------------------------------

    async def _build_structure(
        self,
        organization,
        *,
        run: CampaignGenerationRun,
        campaign: Campaign,
        brief: CampaignBrief,
        context,
        concepts: list[CreativeConcept],
        strategy: CampaignStrategy,
        platform,
        objective,
        duration_days: int,
        currency: str,
        gaps: list[str],
    ) -> None:
        blueprint = await get_orchestrator().build_campaign_structure(
            context,
            CampaignBuilderRequest(
                platform=platform.key,
                platform_label=platform.label,
                objective=objective.key,
                objective_label=objective.label,
                optimization=objective.optimization,
                placements=list(platform.placements),
                requested_name=campaign.name,
                daily_budget=_float(campaign.daily_budget),
                currency=currency,
                duration_days=duration_days,
                concepts=[_concept_payload(c) for c in concepts],
                brief=_brief_payload(brief),
                strategy=strategy.model_dump(),
                known_data_gaps=gaps,
            ),
        )

        by_reference = {c.reference: c for c in concepts}
        # Shares are normalised rather than trusted: a model returning shares
        # that sum to 1.4 must not turn into 40% more daily budget than the user
        # authorised.
        share_total = sum(max(0.0, s.budget_share) for s in blueprint.ad_sets) or 1.0
        daily_budget = campaign.daily_budget

        ad_sets: dict[str, AdSet] = {}
        for spec in blueprint.ad_sets:
            share = max(0.0, spec.budget_share) / share_total
            ad_set = AdSet(
                organization_id=organization.id,
                client_id=run.client_id,
                campaign_id=campaign.id,
                name=spec.name[:255],
                status="draft",
                audience=spec.audience,
                daily_budget=(
                    (daily_budget * Decimal(str(round(share, 4)))).quantize(Decimal("0.01"))
                    if daily_budget is not None
                    else None
                ),
                optimization=spec.optimization or objective.optimization,
                placements=[p for p in spec.placements if p in platform.placements]
                or list(platform.placements[:1]),
            )
            self.db.add(ad_set)
            ad_sets[spec.name] = ad_set
        if not ad_sets:
            # A structure with no ad set is not reviewable. One default ad set
            # keeps the package coherent and is visibly a fallback.
            fallback = AdSet(
                organization_id=organization.id,
                client_id=run.client_id,
                campaign_id=campaign.id,
                name=f"{campaign.name} — Ad set 1"[:255],
                status="draft",
                audience=brief.audience,
                daily_budget=daily_budget,
                optimization=objective.optimization,
                placements=list(platform.placements[:1]),
            )
            self.db.add(fallback)
            ad_sets[fallback.name] = fallback
        await self.db.flush()

        first_ad_set = next(iter(ad_sets.values()))
        ad_count = 0
        for spec in blueprint.ads:
            concept = by_reference.get(spec.concept_id)
            if concept is None:
                # The builder referenced a concept that does not exist. Skipped
                # rather than invented: an ad with no creative behind it would
                # look complete in the preview and be unbuildable.
                continue
            ad_set = ad_sets.get(spec.ad_set_name, first_ad_set)
            asset = await self._primary_asset_id(concept.id)
            ad = Ad(
                organization_id=organization.id,
                client_id=run.client_id,
                ad_set_id=ad_set.id,
                name=spec.name[:255],
                status="draft",
                concept_id=concept.id,
                creative_asset_id=asset,
                headline=spec.headline or concept.headline,
                primary_text=spec.primary_text or concept.primary_text,
                cta=spec.cta or concept.cta,
                destination=spec.destination,
                creative={
                    "concept_reference": concept.reference,
                    "creative_type": spec.creative_type,
                    "image_prompt": concept.image_prompt,
                },
            )
            self.db.add(ad)
            ad_count += 1
        await self.db.flush()

        self._stage(
            run,
            "structure",
            STAGE_COMPLETED,
            completed=ad_count,
            total=max(ad_count, len(blueprint.ads)),
            detail=f"{len(ad_sets)} ad set(s), {ad_count} ad(s).",
        )
        await meter(
            self.db,
            organization_id=organization.id,
            metric=Metric.CAMPAIGN_GENERATION,
            idempotency_key=f"campaign_generation:{run.id}",
            client_id=run.client_id,
            details={"campaign_id": str(campaign.id), "run_id": str(run.id)},
        )
        await self.db.flush()

    async def _primary_asset_id(self, concept_id: UUID) -> UUID | None:
        return await self.db.scalar(
            select(CreativeAsset.id)
            .where(
                CreativeAsset.concept_id == concept_id,
                CreativeAsset.status == "completed",
                CreativeAsset.archived_at.is_(None),
            )
            .order_by(CreativeAsset.created_at.asc())
            .limit(1)
        )

    # ------------------------------------------------------------------
    # Reconciliation and reads
    # ------------------------------------------------------------------

    async def reconcile(self, run: CampaignGenerationRun) -> CampaignGenerationRun:
        """
        Refresh media counts from the job rows and promote the run when done.

        Called from the read path and from the `campaign.reconcile` job, so the
        status is correct whether or not anyone is watching. Counts come from
        `image_jobs`/`video_jobs`, which is why "Images 2/3" is a fact rather
        than an estimate.
        """
        if run.status in TERMINAL_RUN_STATUSES:
            return run

        structure_done = self._stage_status(run, "structure") == STAGE_COMPLETED
        media_pending = False

        for key, model in (("images", ImageJob), ("videos", VideoJob)):
            if self._stage_status(run, key) in {
                STAGE_SKIPPED,
                STAGE_NOT_CONFIGURED,
                STAGE_FAILED,
            }:
                continue
            rows = await self.db.execute(
                select(model.status, func.count()).where(model.run_id == run.id).group_by(model.status)
            )
            counts = {status: int(count) for status, count in rows.all()}
            total = sum(counts.values())
            if not total:
                continue
            completed = counts.get(JobStatus.completed, 0)
            failed = sum(counts.get(s, 0) for s in (JobStatus.failed, JobStatus.cancelled))
            outstanding = total - completed - failed
            media_pending = media_pending or outstanding > 0

            if outstanding:
                status = STAGE_RUNNING
                detail = None
            elif completed:
                status = STAGE_COMPLETED
                detail = f"{failed} failed." if failed else None
            else:
                status = STAGE_FAILED
                detail = "Every generation attempt failed. See the asset errors."
            self._stage(run, key, status, completed=completed, total=total, detail=detail)

        if structure_done and not media_pending:
            run.status = RUN_READY
            run.finished_at = run.finished_at or _now()
            if run.campaign_id:
                campaign = await self.db.get(Campaign, run.campaign_id)
                if campaign is not None and campaign.review_status == REVIEW_GENERATING:
                    campaign.review_status = REVIEW_READY
        await self.db.flush()
        return run

    async def schedule_reconcile(self, run: CampaignGenerationRun) -> None:
        """
        Queue a follow-up reconciliation for a run still waiting on media.

        Without this, a run whose media finishes after the last poll would stay
        GENERATING until someone happened to read it. Skipped when jobs run
        inline, where there is nothing left in flight by definition.
        """
        if get_settings().should_run_jobs_inline or run.status in TERMINAL_RUN_STATUSES:
            return
        from app.jobs.queue import JobQueue

        attempt = int((run.result or {}).get("reconcile_attempt") or 0) + 1
        if attempt > 60:
            return
        run.result = {**(run.result or {}), "reconcile_attempt": attempt}
        flag_modified(run, "result")
        await JobQueue(self.db).enqueue(
            job_type=CAMPAIGN_RECONCILE_JOB,
            payload={"run_id": str(run.id)},
            organization_id=run.organization_id,
            run_after=_now() + timedelta(seconds=min(10 * attempt, 60)),
            max_attempts=1,
            dedupe_key=f"campaign-reconcile:{run.id}:{attempt}",
        )

    async def get_run(self, organization_id: UUID, run_id: UUID) -> CampaignGenerationRunOut:
        run = await self._run(organization_id, run_id)
        await self.reconcile(run)
        return await self._run_out(run)

    async def list_runs(
        self, organization_id: UUID, *, client_id: UUID | None = None, limit: int = 20
    ) -> list[CampaignGenerationRunOut]:
        stmt = (
            select(CampaignGenerationRun)
            .where(CampaignGenerationRun.organization_id == organization_id)
            .order_by(CampaignGenerationRun.created_at.desc())
            .limit(min(limit, 100))
        )
        if client_id:
            stmt = stmt.where(CampaignGenerationRun.client_id == client_id)
        runs = list(await self.db.scalars(stmt))
        return [await self._run_out(run) for run in runs]

    async def package(self, organization, campaign_id: UUID) -> CampaignPackageOut:
        """The full reviewable package for one campaign."""
        campaign = await self._campaign(organization.id, campaign_id)
        brief = await self.db.get(CampaignBrief, campaign.brief_id) if campaign.brief_id else None
        run = await self.db.scalar(
            select(CampaignGenerationRun)
            .where(
                CampaignGenerationRun.organization_id == organization.id,
                CampaignGenerationRun.campaign_id == campaign.id,
            )
            .order_by(CampaignGenerationRun.created_at.desc())
            .limit(1)
        )
        if run is not None:
            await self.reconcile(run)

        concepts = list(
            await self.db.scalars(
                select(CreativeConcept)
                .where(
                    CreativeConcept.organization_id == organization.id,
                    CreativeConcept.campaign_id == campaign.id,
                )
                .order_by(CreativeConcept.reference.asc(), CreativeConcept.created_at.asc())
            )
        )
        ad_sets = list(
            await self.db.scalars(
                select(AdSet)
                .where(AdSet.organization_id == organization.id, AdSet.campaign_id == campaign.id)
                .order_by(AdSet.created_at.asc())
            )
        )
        ads: list[Ad] = []
        if ad_sets:
            ads = list(
                await self.db.scalars(
                    select(Ad)
                    .where(
                        Ad.organization_id == organization.id,
                        Ad.ad_set_id.in_([a.id for a in ad_sets]),
                    )
                    .order_by(Ad.created_at.asc())
                )
            )

        strategy = None
        if brief and brief.strategy:
            try:
                strategy = CampaignStrategy.model_validate(brief.strategy)
            except Exception:  # noqa: BLE001 — an old brief shape must not break the read
                strategy = None

        limitations = list((run.data_limitations if run else None) or (brief.data_limitations if brief else []))
        return CampaignPackageOut(
            campaign=CampaignSummaryOut.model_validate(
                {
                    **{
                        field: getattr(campaign, field)
                        for field in (
                            "id",
                            "client_id",
                            "name",
                            "platform",
                            "objective",
                            "review_status",
                            "status",
                            "audience",
                            "total_budget",
                            "daily_budget",
                            "monthly_budget",
                            "currency",
                            "generated_by_ai",
                            "created_at",
                        )
                    },
                    "data_source": getattr(campaign.data_source, "value", str(campaign.data_source)),
                }
            ),
            brief=CampaignBriefOut.model_validate(brief) if brief else None,
            strategy=strategy,
            concepts=[await self._concept_out(concept) for concept in concepts],
            ad_sets=[AdSetOut.model_validate(a) for a in ad_sets],
            ads=[AdOut.model_validate(a) for a in ads],
            approval=self._approval_out(campaign),
            run=await self._run_out(run) if run else None,
            data_limitations=limitations,
            media=self.media_status(organization),
        )

    # ------------------------------------------------------------------
    # Approval
    # ------------------------------------------------------------------

    async def approve(
        self, organization, *, user_id: UUID, campaign_id: UUID, comment: str | None
    ) -> CampaignPackageOut:
        """
        Record human approval and move the campaign to READY_TO_PUBLISH.

        READY_TO_PUBLISH is a statement about the package, not about any platform:
        publishing is not implemented, nothing is scheduled, and no money moves.
        Permission is enforced at the route with `action_approve`.
        """
        campaign = await self._campaign(organization.id, campaign_id)
        if campaign.review_status == REVIEW_GENERATING:
            raise CampaignStateConflict(
                "This campaign is still being generated and cannot be approved yet."
            )
        if campaign.review_status in {REVIEW_APPROVED, REVIEW_READY_TO_PUBLISH}:
            raise CampaignStateConflict("This campaign has already been approved.")

        campaign.review_status = REVIEW_READY_TO_PUBLISH
        campaign.approved_by = user_id
        campaign.approved_at = _now()
        campaign.approval_comment = comment
        # A rejection that was later approved should not keep reading as rejected.
        campaign.rejected_by = None
        campaign.rejected_at = None
        campaign.rejection_reason = None
        await self.db.flush()
        await self._audit(campaign, user_id, action="campaign.approve", detail=comment)
        return await self.package(organization, campaign.id)

    async def reject(
        self, organization, *, user_id: UUID, campaign_id: UUID, reason: str
    ) -> CampaignPackageOut:
        campaign = await self._campaign(organization.id, campaign_id)
        if campaign.review_status == REVIEW_GENERATING:
            raise CampaignStateConflict(
                "This campaign is still being generated and cannot be rejected yet."
            )
        campaign.review_status = REVIEW_REJECTED
        campaign.rejected_by = user_id
        campaign.rejected_at = _now()
        campaign.rejection_reason = reason
        campaign.approved_by = None
        campaign.approved_at = None
        campaign.approval_comment = None
        await self.db.flush()
        await self._audit(campaign, user_id, action="campaign.reject", detail=reason)
        return await self.package(organization, campaign.id)

    async def _audit(self, campaign: Campaign, user_id: UUID, *, action: str, detail: str | None) -> None:
        from app.security.audit import write_audit

        await write_audit(
            self.db,
            action=action,
            organization_id=campaign.organization_id,
            user_id=user_id,
            resource_type="campaign",
            resource_id=str(campaign.id),
            details={"review_status": campaign.review_status, "comment": detail},
        )

    # ------------------------------------------------------------------
    # Regeneration
    # ------------------------------------------------------------------

    async def regenerate_concept_media(
        self, organization, *, concept_id: UUID, request: ConceptRegenerateRequest
    ) -> list[ConceptAssetOut]:
        """
        Re-run media generation for one concept, reusing its stored prompts.

        Deliberately does not re-run the AI stages: the reviewer asked for
        another render of an approved idea, not a different idea.
        """
        concept = await self._concept(organization.id, concept_id)
        limits = registry.generation_limits()
        images = min(request.image_quantity, limits.max_images_per_concept)
        videos = min(request.video_quantity, limits.max_videos_per_concept)
        if not images and not videos:
            raise InvalidCampaignRequest("Request at least one image or video.")

        if images:
            await self._require_quota(organization.id, Metric.IMAGE_GENERATION, images)
        if videos:
            await self._require_quota(organization.id, Metric.VIDEO_GENERATION, videos)

        media = MediaGenerationService(self.db)
        batch = uuid4().hex[:8]
        ratio = request.aspect_ratio or (concept.aspect_ratios or ["1:1"])[0]

        if images:
            prompt = _image_prompt(concept)
            if not prompt:
                raise InvalidCampaignRequest(
                    "This concept has no image prompt, so there is nothing to regenerate."
                )
            await media.enqueue_images(
                organization,
                client_id=concept.client_id,
                campaign_id=concept.campaign_id,
                prompt=prompt,
                aspect_ratio=ratio,
                quantity=images,
                platform=concept.platform,
                concept_id=concept.id,
                idempotency_key=f"concept:{concept.id}:{batch}",
                max_quantity=limits.max_images_per_concept,
            )
        if videos:
            if not concept.video_prompt:
                raise InvalidCampaignRequest(
                    "This concept has no video prompt, so there is nothing to regenerate."
                )
            for index in range(videos):
                await media.enqueue_video(
                    organization,
                    client_id=concept.client_id,
                    campaign_id=concept.campaign_id,
                    prompt=concept.video_prompt,
                    aspect_ratio=_video_ratio(concept),
                    platform=concept.platform,
                    concept_id=concept.id,
                    idempotency_key=f"concept:{concept.id}:{batch}:video:{index}",
                )
        return await self._concept_assets(concept)

    async def archive_concept(self, organization, *, concept_id: UUID, archived: bool) -> CreativeConceptOut:
        concept = await self._concept(organization.id, concept_id)
        concept.archived_at = _now() if archived else None
        concept.status = "ARCHIVED" if archived else "READY"
        await self.db.flush()
        return await self._concept_out(concept)

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    async def _run(self, organization_id: UUID, run_id: UUID) -> CampaignGenerationRun:
        run = await self.db.scalar(
            select(CampaignGenerationRun).where(
                CampaignGenerationRun.id == run_id,
                CampaignGenerationRun.organization_id == organization_id,
            )
        )
        if run is None:
            raise _not_found("GENERATION_RUN_NOT_FOUND")
        return run

    async def _campaign(self, organization_id: UUID, campaign_id: UUID) -> Campaign:
        campaign = await self.db.scalar(
            select(Campaign).where(
                Campaign.id == campaign_id, Campaign.organization_id == organization_id
            )
        )
        if campaign is None:
            raise _not_found("CAMPAIGN_NOT_FOUND")
        return campaign

    async def _concept(self, organization_id: UUID, concept_id: UUID) -> CreativeConcept:
        concept = await self.db.scalar(
            select(CreativeConcept).where(
                CreativeConcept.id == concept_id,
                CreativeConcept.organization_id == organization_id,
            )
        )
        if concept is None:
            raise _not_found("CONCEPT_NOT_FOUND")
        return concept

    async def _used_references(self, parent: CreativeConcept) -> list[str]:
        siblings = await self.db.scalars(
            select(CreativeConcept.reference).where(
                CreativeConcept.organization_id == parent.organization_id,
                CreativeConcept.campaign_id == parent.campaign_id,
            )
        )
        variations = await self.db.scalars(
            select(CreativeVariation.reference).where(
                CreativeVariation.organization_id == parent.organization_id,
                CreativeVariation.parent_concept_id == parent.id,
            )
        )
        return sorted({*(siblings or []), *(variations or []), parent.reference})

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    async def _run_out(self, run: CampaignGenerationRun) -> CampaignGenerationRunOut:
        return CampaignGenerationRunOut(
            id=run.id,
            organization_id=run.organization_id,
            client_id=run.client_id,
            brief_id=run.brief_id,
            campaign_id=run.campaign_id,
            status=run.status,
            platform=run.platform,
            objective=run.objective,
            stages=list(run.stages or []),
            result=dict(run.result or {}),
            data_limitations=list(run.data_limitations or []),
            error=run.error,
            error_code=run.error_code,
            background_job_id=run.background_job_id,
            concept_quantity=run.concept_quantity,
            image_quantity=run.image_quantity,
            video_quantity=run.video_quantity,
            variation_quantity=run.variation_quantity,
            demo_mode=run.demo_mode,
            started_at=run.started_at,
            finished_at=run.finished_at,
            created_at=run.created_at,
            terminal=run.status in TERMINAL_RUN_STATUSES,
            poll_url=f"/api/v1/campaign-generation/runs/{run.id}",
        )

    async def _concept_out(self, concept: CreativeConcept) -> CreativeConceptOut:
        variations = list(
            await self.db.scalars(
                select(CreativeVariation)
                .where(CreativeVariation.parent_concept_id == concept.id)
                .order_by(CreativeVariation.created_at.asc())
            )
        )
        out = CreativeConceptOut.model_validate(concept)
        out.assets = await self._concept_assets(concept)
        out.variations = [await self._variation_out(v) for v in variations]
        return out

    async def _variation_out(self, variation: CreativeVariation) -> CreativeVariationOut:
        out = CreativeVariationOut.model_validate(variation)
        out.assets = await self._assets_for(variation_id=variation.id)
        return out

    async def _concept_assets(self, concept: CreativeConcept) -> list[ConceptAssetOut]:
        return await self._assets_for(concept_id=concept.id)

    async def _assets_for(
        self, *, concept_id: UUID | None = None, variation_id: UUID | None = None
    ) -> list[ConceptAssetOut]:
        """
        Every media job for this concept or variation, in whatever state it is in.

        Jobs are the source of truth rather than assets, so a QUEUED, GENERATING
        or FAILED attempt is visible instead of silently absent. A url is only
        attached when a completed asset's key is inside the owning tenant's
        prefix.
        """
        out: list[ConceptAssetOut] = []
        for kind, model in (("image", ImageJob), ("video", VideoJob)):
            predicate = (
                model.concept_id == concept_id
                if concept_id is not None
                else model.variation_id == variation_id
            )
            jobs = list(
                await self.db.scalars(
                    select(model).where(predicate).order_by(model.created_at.asc())
                )
            )
            for job in jobs:
                asset = (
                    await self.db.get(CreativeAsset, job.creative_asset_id)
                    if job.creative_asset_id
                    else None
                )
                url = None
                if (
                    asset is not None
                    and asset.storage_key
                    and key_belongs_to_organization(asset.storage_key, asset.organization_id)
                ):
                    url = f"/api/v1/creative/media/{asset.id}"
                out.append(
                    ConceptAssetOut(
                        id=asset.id if asset else None,
                        job_id=job.id,
                        kind=kind,
                        status=str(getattr(job.status, "value", job.status)).upper(),
                        url=url,
                        mime_type=asset.mime_type if asset else None,
                        width=asset.width if asset else None,
                        height=asset.height if asset else None,
                        duration_seconds=getattr(asset, "duration_seconds", None) if asset else None,
                        aspect_ratio=job.aspect_ratio,
                        provider=job.provider,
                        error=job.error,
                        error_code=job.error_code,
                        retryable=bool(job.retryable),
                        demo=bool(asset and asset.data_source == "demo"),
                    )
                )
        return out

    def _approval_out(self, campaign: Campaign) -> CampaignApprovalOut:
        return CampaignApprovalOut(
            review_status=campaign.review_status,
            approved_by=campaign.approved_by,
            approved_at=campaign.approved_at,
            approval_comment=campaign.approval_comment,
            rejected_by=campaign.rejected_by,
            rejected_at=campaign.rejected_at,
            rejection_reason=campaign.rejection_reason,
            external_id=campaign.external_id,
            can_approve=campaign.review_status
            in {REVIEW_READY, REVIEW_REJECTED, REVIEW_DRAFT},
        )

    # ------------------------------------------------------------------
    # Stage bookkeeping
    # ------------------------------------------------------------------

    def _stage(
        self,
        run: CampaignGenerationRun,
        key: str,
        status: str,
        *,
        detail: str | None = None,
        completed: int | None = None,
        total: int | None = None,
    ) -> None:
        stages = list(run.stages or [])
        for stage in stages:
            if stage.get("key") != key:
                continue
            stage["status"] = status
            if detail is not None:
                stage["detail"] = detail
            if completed is not None:
                stage["completed"] = completed
            if total is not None:
                stage["total"] = total
            break
        run.stages = stages
        # JSON columns are mutated in place; without this the change is invisible
        # to the session and the UI polls a stage list that never advances.
        flag_modified(run, "stages")

    def _stage_status(self, run: CampaignGenerationRun, key: str) -> str:
        for stage in run.stages or []:
            if stage.get("key") == key:
                return str(stage.get("status") or STAGE_PENDING)
        return STAGE_PENDING

    async def _fail_run(
        self, run: CampaignGenerationRun, *, code: str, message: str, detail: str | None
    ) -> None:
        run.status = RUN_FAILED
        run.error_code = code
        # `detail` is an exception type or provider message, never a traceback or
        # a credential, and it is what makes a support conversation possible.
        run.error = f"{message} ({detail})" if detail else message
        run.finished_at = _now()
        for stage in run.stages or []:
            if stage.get("status") == STAGE_RUNNING:
                stage["status"] = STAGE_FAILED
                stage["detail"] = message
        flag_modified(run, "stages")
        if run.campaign_id:
            campaign = await self.db.get(Campaign, run.campaign_id)
            if campaign is not None and campaign.review_status == REVIEW_GENERATING:
                # A half-generated campaign is a draft, not something awaiting
                # review: presenting it for approval would invite sign-off on an
                # incomplete package.
                campaign.review_status = REVIEW_DRAFT
        await self.db.flush()

    def _historical_evidence(self, context) -> list[dict]:
        rows: list[dict] = []
        rows.extend(context.historical_campaign_performance or [])
        rows.extend(context.historical_content_performance or [])
        if context.lead_performance:
            rows.append({"lead_performance": context.lead_performance})
        if context.available_metrics:
            rows.append({"recorded_totals": context.available_metrics})
        return rows


# --------------------------------------------------------------------------
# Module helpers
# --------------------------------------------------------------------------


def _not_found(code: str):
    from fastapi import HTTPException, status

    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=code)


def _initial_stages(
    *, concept_quantity: int, image_quantity: int, video_quantity: int, variation_quantity: int
) -> list[dict]:
    totals = {
        "copy": concept_quantity,
        "concepts": concept_quantity,
        "images": image_quantity,
        "videos": video_quantity,
        "variations": variation_quantity,
    }
    return [
        {
            "key": key,
            "label": label,
            "status": STAGE_PENDING,
            "detail": None,
            "completed": 0,
            "total": totals.get(key, 0),
        }
        for key, label in STAGE_PLAN
    ]


def _merge_limitations(existing: list[str], added: Iterable[str]) -> list[str]:
    """Union, order preserved. An agent restating a known gap should not double it."""
    return list(dict.fromkeys([*existing, *[str(item) for item in added if item]]))


def _brief_payload(brief: CampaignBrief) -> dict:
    return {
        "campaign_name": brief.campaign_name,
        "platform": brief.platform,
        "objective": brief.objective,
        "offer": brief.offer,
        "audience": brief.audience,
        "pain_points": brief.pain_points or [],
        "value_proposition": brief.value_proposition,
        "messaging_angle": brief.messaging_angle,
        "tone": brief.tone,
        "brand_constraints": brief.brand_constraints or [],
        "success_metrics": brief.success_metrics or [],
        "creative_direction": brief.creative_direction,
        "cta": brief.cta,
        "data_limitations": brief.data_limitations or [],
    }


def _concept_payload(concept: CreativeConcept) -> dict:
    return {
        "concept_id": concept.reference,
        "reference": concept.reference,
        "angle": concept.angle,
        "hook": concept.hook,
        "primary_text": concept.primary_text,
        "headline": concept.headline,
        "description": concept.description,
        "cta": concept.cta,
        "tone": concept.tone,
        "audience": concept.audience,
        "objective": concept.objective,
        "visual_direction": concept.visual_direction or {},
        "image_prompt": concept.image_prompt,
        "video_prompt": concept.video_prompt,
        "aspect_ratios": concept.aspect_ratios or [],
        "negative_constraints": concept.negative_constraints or [],
    }


def _ratio_payload(key: str) -> dict:
    spec = registry.aspect_ratio(key)
    return {
        "key": spec.key,
        "width": spec.width,
        "height": spec.height,
        "orientation": spec.orientation,
        "usage": spec.usage,
    }


def _negative_constraints(spec) -> list[str]:
    """
    Baseline constraints are added in code, not left to the model.

    These are the failures that make an asset unusable — garbled text, invented
    logos, fabricated ratings — so they apply to every generation regardless of
    what the concept agent remembered to return.
    """
    from app.ai.agents.creative_concept_agent import BASELINE_NEGATIVE_CONSTRAINTS

    supplied = list(spec.negative_constraints) if spec else []
    return list(dict.fromkeys([*supplied, *BASELINE_NEGATIVE_CONSTRAINTS]))


def _compose_prompt(prompt: str, negatives: list[str]) -> str:
    """
    Fold negative constraints into the prompt text.

    Image APIs differ on whether they accept a separate negative prompt, and
    several accept none at all. Appending them keeps the constraint effective
    across every provider behind the abstraction.
    """
    if not negatives:
        return prompt
    return f"{prompt}\n\nAvoid: {'; '.join(negatives)}."


def _image_prompt(concept: CreativeConcept) -> str | None:
    if not concept.image_prompt:
        return None
    return _compose_prompt(concept.image_prompt, list(concept.negative_constraints or []))


def _video_ratio(concept: CreativeConcept) -> str:
    """Prefer a vertical ratio for video when the concept allows one."""
    ratios = list(concept.aspect_ratios or [])
    for preferred in ("9:16", "4:5", "1:1", "16:9"):
        if preferred in ratios:
            return preferred
    try:
        return registry.platform(concept.platform).default_video_ratio
    except registry.UnknownCampaignOption:
        return "9:16"


def _round_robin(concepts: list[CreativeConcept], quantity: int):
    """Spread `quantity` items across concepts: A, B, C, A, B …"""
    if not concepts:
        return
    for index in range(quantity):
        yield index, concepts[index % len(concepts)]


def _next_reference(taken: set[str]) -> str:
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if letter not in taken:
            return letter
    return uuid4().hex[:4].upper()
