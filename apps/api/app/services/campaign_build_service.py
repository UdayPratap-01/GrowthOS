"""AI Campaign Builder + one-click Marketing Autopilot workflows."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.ai.agents.campaign_planner_agent import CampaignPlanRequest
from app.ai.agents.creative_agent import CreativeRequest
from app.ai.agents.image_creative_agent import ImageCreativeRequest
from app.ai.agents.video_agent import VideoAgentRequest
from app.ai.orchestrator import get_orchestrator
from app.core.mode import effective_demo_mode
from app.generation import get_image_provider, get_video_provider
from app.models.automation import AutopilotRun, CreativeAsset
from app.models.enums import AIActionType, Priority, RiskLevel
from app.schemas.autopilot import (
    AIActionCreate,
    AutopilotRunOut,
    AutopilotRunRequest,
    CampaignBuildRequest,
    CampaignBuildResult,
    CreativeAssetOut,
    CreativeVariationsRequest,
)
from app.services.action_service import ActionService
from app.services.autonomy_service import AutonomyService
from app.services.client_service import ClientService


def _step(key: str, label: str, status: str = "pending", detail: str | None = None) -> dict:
    return {"key": key, "label": label, "status": status, "detail": detail}


class CampaignBuildService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def build_campaign(
        self, organization, data: CampaignBuildRequest, *, user_id: UUID
    ) -> CampaignBuildResult:
        demo = effective_demo_mode(organization)
        context = await ClientService(self.db).build_client_context(organization, data.client_id)
        settings = await AutonomyService(self.db).get_effective(organization.id, data.client_id)

        run = AutopilotRun(
            organization_id=organization.id,
            client_id=data.client_id,
            run_type="campaign_build",
            status="RUNNING",
            goal=data.objective,
            budget=data.budget,
            duration_days=data.duration_days,
            platforms=list(data.platforms),
            autonomy_mode=settings.autonomy_mode.value,
            request=data.model_dump(mode="json"),
            steps=[
                _step("context", "Load client context"),
                _step("competitors", "Analyze competitors"),
                _step("strategy", "Create campaign strategy"),
                _step("structure", "Build campaign structure"),
                _step("images", "Generate image concepts"),
                _step("videos", "Generate video concepts"),
                _step("variations", "Generate creative variations"),
                _step("actions", "Create structured actions"),
                _step("approval", "Approval / autonomy gate"),
            ],
            demo_mode=demo,
            started_at=datetime.now(timezone.utc),
        )
        self.db.add(run)
        await self.db.flush()

        action_ids: list[str] = []
        orch = get_orchestrator()

        self._mark(run, "context", "completed", f"{context.business_name}")
        await self.db.flush()

        try:
            competitor = await orch.competitor_insight(context)
            self._mark(
                run,
                "competitors",
                "completed",
                f"{len(competitor.observations)} observations ({competitor.data_label})",
            )
        except Exception as exc:  # noqa: BLE001
            self._mark(run, "competitors", "blocked", str(exc)[:200])

        plan = await orch.plan_campaign(
            context,
            CampaignPlanRequest(
                objective=data.objective,
                budget=float(data.budget) if data.budget is not None else None,
                duration_days=data.duration_days,
                offer=data.offer,
                audience=data.target_audience,
                platforms=list(data.platforms),
                location=data.location,
                image_quantity=data.image_quantity,
                video_quantity=data.video_quantity,
                variation_quantity=data.variation_quantity,
                cta=data.cta,
                campaign_goal=data.campaign_goal,
            ),
        )
        self._mark(run, "strategy", "completed", plan.messaging_strategy[:160] if plan.messaging_strategy else "Strategy drafted")
        self._mark(
            run,
            "structure",
            "completed",
            f"{len(plan.ad_sets)} ad sets, {len(plan.ads)} ads planned",
        )

        # Image prompts → concepts (provider may be NOT CONFIGURED)
        image_pack = await orch.image_creatives(
            context,
            ImageCreativeRequest(
                objective=data.objective,
                platform=data.platforms[0] if data.platforms else "instagram",
                offer=data.offer,
                count=data.image_quantity,
            ),
        )
        from app.services.media_generation_service import MediaGenerationService

        media = MediaGenerationService(self.db)
        image_provider = get_image_provider()
        image_detail = f"{len(image_pack.prompts)} prompts via {image_provider.name}"
        generated_images = 0
        for item in image_pack.prompts[: data.image_quantity]:
            concept = CreativeAsset(
                organization_id=organization.id,
                client_id=data.client_id,
                name=(item.headline_suggestion or item.style)[:255],
                asset_type="image_concept",
                platform=data.platforms[0] if data.platforms else None,
                prompt=item.prompt,
                provider="creative_agent",
                status="draft",
                content=item.model_dump(),
                meta={"run_id": str(run.id), "style": item.style},
                data_source="demo" if demo else "live",
            )
            self.db.add(concept)
            await self.db.flush()
            if image_provider.configured():
                gen = await media.enqueue_images(
                    organization,
                    client_id=data.client_id,
                    prompt=item.prompt,
                    aspect_ratio="1:1",
                    quantity=1,
                    platform=data.platforms[0] if data.platforms else None,
                    idempotency_key=f"build:{run.id}:img:{concept.id}",
                )
                if gen.get("status") == "COMPLETED":
                    generated_images += 1
            else:
                image_detail = "IMAGE GENERATION NOT CONFIGURED — concepts stored only"
        if image_provider.configured():
            image_detail = f"{generated_images}/{min(len(image_pack.prompts), data.image_quantity)} images stored"
            self._mark(
                run,
                "images",
                "completed" if generated_images else "blocked",
                image_detail,
            )
        else:
            self._mark(run, "images", "blocked", image_detail)

        video_pack = await orch.video_concepts(
            context,
            VideoAgentRequest(
                objective=data.objective,
                platform=data.platforms[0] if data.platforms else "instagram",
                offer=data.offer,
                count=data.video_quantity,
            ),
        )
        video_provider = get_video_provider()
        video_detail = f"{len(video_pack.concepts)} scripts via {video_provider.name}"
        generated_videos = 0
        for concept in video_pack.concepts[: data.video_quantity]:
            row = CreativeAsset(
                organization_id=organization.id,
                client_id=data.client_id,
                name=concept.title[:255],
                asset_type="video_concept",
                platform=data.platforms[0] if data.platforms else None,
                prompt=concept.script,
                provider="creative_agent",
                status="draft",
                content=concept.model_dump(),
                meta={"run_id": str(run.id)},
                data_source="demo" if demo else "live",
            )
            self.db.add(row)
            await self.db.flush()
            if video_provider.configured():
                gen = await media.enqueue_video(
                    organization,
                    client_id=data.client_id,
                    prompt=getattr(concept, "script", None) or concept.title,
                    aspect_ratio="9:16",
                    duration_seconds=getattr(concept, "duration_seconds", 10) or 10,
                    platform=data.platforms[0] if data.platforms else None,
                    idempotency_key=f"build:{run.id}:vid:{row.id}",
                )
                if gen.get("status") == "COMPLETED":
                    generated_videos += 1
                elif gen.get("status") in {"SUBMITTED", "PROCESSING", "QUEUED"}:
                    video_detail = f"Video jobs in progress ({gen.get('status')})"
            else:
                video_detail = "VIDEO GENERATION NOT CONFIGURED — concepts stored only"
        if video_provider.configured():
            if generated_videos:
                self._mark(run, "videos", "completed", f"{generated_videos} videos stored")
            else:
                self._mark(run, "videos", "blocked", video_detail or "Videos not yet completed")
        else:
            self._mark(run, "videos", "blocked", video_detail)

        variations = []
        for h in (plan.hooks or [])[:5]:
            variations.append({"type": "hook", "text": h})
        for h in (plan.headlines or [])[:5]:
            variations.append({"type": "headline", "text": h})
        for t in (plan.primary_texts or [])[:5]:
            variations.append({"type": "primary_text", "text": t})
        for c in (plan.ctas or [])[:5]:
            variations.append({"type": "cta", "text": c})
        variations = variations[: data.variation_quantity]

        actions = ActionService(self.db)
        try:
            var_action = await actions.create(
                organization.id,
                AIActionCreate(
                    action_type=AIActionType.generate_creative_variations,
                    client_id=data.client_id,
                    agent="CampaignPlannerAgent",
                    platform=data.platforms[0] if data.platforms else None,
                    description=f"Generate {len(variations)} creative variations",
                    reason="Campaign builder variation pack",
                    evidence=[{"run_id": str(run.id)}],
                    priority=Priority.medium,
                    payload={"variations": variations, "run_id": str(run.id)},
                ),
                user_id=user_id,
            )
            action_ids.append(str(var_action.id))
            self._mark(run, "variations", "completed", f"{len(variations)} variation drafts queued")
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            self._mark(run, "variations", "blocked", detail[:180])

        # Campaign create action — estimated_cost uses daily budget (BudgetGuard) not full duration total.
        total_budget = data.budget or Decimal("0")
        daily_budget = (
            (total_budget / Decimal(max(data.duration_days, 1))).quantize(Decimal("0.01"))
            if total_budget > 0
            else None
        )
        # Cap proposal to org safety limits so builder still produces a reviewable action.
        if daily_budget is not None:
            cap = min(settings.maximum_daily_ad_spend, settings.maximum_campaign_budget)
            if daily_budget > cap:
                daily_budget = cap

        campaign_action = None
        try:
            campaign_action = await actions.create(
                organization.id,
                AIActionCreate(
                    action_type=AIActionType.create_campaign,
                    client_id=data.client_id,
                    agent="CampaignPlannerAgent",
                    platform=data.platforms[0] if data.platforms else "meta",
                    description=f"Create campaign '{plan.name}' — {data.objective}",
                    reason=plan.messaging_strategy or "AI campaign builder proposal",
                    evidence=[
                        {"run_id": str(run.id)},
                        {"ad_sets": len(plan.ad_sets)},
                        {"ads": len(plan.ads)},
                        {"insufficient_data": plan.insufficient_data},
                        {"total_budget_requested": str(total_budget)},
                        {"daily_budget_proposed": str(daily_budget) if daily_budget is not None else None},
                    ],
                    expected_impact="Campaign structure ready; live creation only after approval + platform confirmation",
                    estimated_cost=daily_budget,
                    priority=Priority.high,
                    risk_level=RiskLevel.high,
                    payload={
                        "plan": plan.model_dump(),
                        "run_id": str(run.id),
                        "name": plan.name,
                        "objective": data.objective,
                        "total_budget": str(total_budget),
                        "daily_budget": str(daily_budget) if daily_budget is not None else None,
                    },
                ),
                user_id=user_id,
            )
            action_ids.append(str(campaign_action.id))
            self._mark(run, "actions", "completed", f"{len(action_ids)} structured actions created")
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            self._mark(run, "actions", "blocked", f"ACTION BLOCKED — {detail[:180]}")
        except Exception as exc:  # noqa: BLE001
            self._mark(run, "actions", "blocked", f"ACTION BLOCKED — {str(exc)[:180]}")

        if campaign_action and (campaign_action.requires_approval or campaign_action.status.value == "PENDING"):
            self._mark(run, "approval", "blocked", "WAITING_FOR_APPROVAL — campaign not published")
            run.status = "AWAITING_APPROVAL"
        elif campaign_action:
            self._mark(run, "approval", "completed", "Autonomy allowed progression (still subject to platform APIs)")
            run.status = "COMPLETED"
        else:
            self._mark(run, "approval", "blocked", "Campaign action not created — review budget/permissions")
            run.status = "FAILED"

        run.action_ids = action_ids
        run.result = {
            "plan_name": plan.name,
            "image_provider": image_provider.name,
            "video_provider": video_provider.name,
            "demo_mode": demo,
            "note": "No live campaign IDs invented. Execute approved actions via ExecutionEngine.",
        }
        run.finished_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(run)

        return CampaignBuildResult(
            run=AutopilotRunOut.model_validate(run),
            action_ids=action_ids,
            plan=plan.model_dump(),
            message=(
                "Campaign proposal built. Review Approvals before any platform execution. "
                + ("DEMO DATA may be present. " if demo else "")
                + ("IMAGE GENERATION NOT CONFIGURED. " if not image_provider.configured() else "")
                + ("VIDEO GENERATION NOT CONFIGURED." if not video_provider.configured() else "")
            ).strip(),
        )

    async def run_autopilot(
        self, organization, data: AutopilotRunRequest, *, user_id: UUID
    ) -> AutopilotRunOut:
        demo = effective_demo_mode(organization)
        context = await ClientService(self.db).build_client_context(organization, data.client_id)
        settings = await AutonomyService(self.db).get_effective(organization.id, data.client_id)
        mode = data.autonomy_mode or settings.autonomy_mode.value

        default_steps = [
            _step("analyze_client", "Client analyzed"),
            _step("analyze_history", "Historical performance analyzed"),
            _step("competitors", "Competitors reviewed"),
            _step("strategy", "Strategy created"),
            _step("campaign", "Campaign structure created"),
            _step("image_concepts", "Image concepts created"),
            _step("video_concepts", "Video concepts created"),
            _step("generate_images", "Generating images"),
            _step("generate_videos", "Generating videos"),
            _step("approval", "Campaign awaiting approval"),
            _step("publishing", "Publishing"),
            _step("monitoring", "Monitoring"),
            _step("optimization", "Optimization"),
        ]

        run = AutopilotRun(
            organization_id=organization.id,
            client_id=data.client_id,
            run_type="marketing_autopilot",
            status="RUNNING",
            goal=data.goal,
            budget=data.budget,
            duration_days=data.duration_days,
            platforms=list(data.platforms),
            autonomy_mode=mode,
            request=data.model_dump(mode="json"),
            steps=default_steps,
            demo_mode=demo,
            started_at=datetime.now(timezone.utc),
        )
        self.db.add(run)
        await self.db.flush()

        orch = get_orchestrator()
        action_ids: list[str] = []

        self._mark(run, "analyze_client", "completed", context.business_name)
        metrics = context.available_metrics or {}
        if metrics:
            self._mark(run, "analyze_history", "completed", "ACTUAL DATA available in context metrics")
        else:
            self._mark(run, "analyze_history", "completed", "INSUFFICIENT DATA — no historical metrics")

        try:
            await orch.competitor_insight(context)
            self._mark(run, "competitors", "completed", "Competitor notes (AI ESTIMATE where speculative)")
        except Exception as exc:  # noqa: BLE001
            self._mark(run, "competitors", "blocked", str(exc)[:160])

        strategy = await orch.generate_strategy(context, title=f"Autopilot — {data.goal}")
        self._mark(run, "strategy", "completed", getattr(strategy, "title", None) or "Strategy draft stored")

        build = await self.build_campaign(
            organization,
            CampaignBuildRequest(
                client_id=data.client_id,
                objective=data.goal,
                budget=data.budget,
                duration_days=data.duration_days,
                platforms=data.platforms,
                offer=data.offer,
                target_audience=data.target_audience,
                image_quantity=data.image_quantity,
                video_quantity=data.video_quantity,
                variation_quantity=data.variation_quantity,
                cta=data.cta,
            ),
            user_id=user_id,
        )
        action_ids.extend(build.action_ids)
        self._mark(run, "campaign", "completed", "Structure + proposal actions created")
        self._mark(run, "image_concepts", "completed", "See campaign build assets")
        self._mark(run, "video_concepts", "completed", "See campaign build assets")

        img = get_image_provider()
        probe = await img.generate_image(prompt=f"{context.business_name} {data.goal} hero creative")
        if probe.success:
            self._mark(run, "generate_images", "completed", probe.message)
        else:
            self._mark(
                run,
                "generate_images",
                "blocked",
                probe.error or probe.message or "IMAGE GENERATION NOT CONFIGURED",
            )

        vid = get_video_provider()
        vprobe = await vid.generate_video(prompt=f"{context.business_name} {data.goal} short ad")
        if vprobe.success:
            self._mark(run, "generate_videos", "completed", vprobe.message)
        else:
            self._mark(
                run,
                "generate_videos",
                "blocked",
                vprobe.error or vprobe.message or "VIDEO GENERATION NOT CONFIGURED",
            )

        self._mark(run, "approval", "blocked", "WAITING_FOR_APPROVAL — no silent publish")
        self._mark(run, "publishing", "blocked", "NOT STARTED — requires approved execute + connected platform")
        self._mark(run, "monitoring", "pending", "Starts after verified publish")
        self._mark(run, "optimization", "pending", "Starts after monitoring has ACTUAL DATA")

        run.action_ids = action_ids
        run.status = "AWAITING_APPROVAL"
        run.result = {
            "build_run_id": str(build.run.id),
            "demo_mode": demo,
            "message": build.message,
        }
        run.finished_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(run)
        return AutopilotRunOut.model_validate(run)

    async def get_run(self, organization_id: UUID, run_id: UUID) -> AutopilotRunOut:
        row = await self.db.scalar(
            select(AutopilotRun).where(
                AutopilotRun.id == run_id, AutopilotRun.organization_id == organization_id
            )
        )
        if not row:
            raise ValueError("RUN_NOT_FOUND")
        return AutopilotRunOut.model_validate(row)

    async def list_runs(self, organization_id: UUID, client_id: UUID | None = None) -> list[AutopilotRunOut]:
        stmt = (
            select(AutopilotRun)
            .where(AutopilotRun.organization_id == organization_id)
            .order_by(AutopilotRun.created_at.desc())
            .limit(50)
        )
        if client_id:
            stmt = stmt.where(AutopilotRun.client_id == client_id)
        rows = (await self.db.execute(stmt)).scalars().all()
        return [AutopilotRunOut.model_validate(r) for r in rows]

    async def generate_variations(
        self, organization, data: CreativeVariationsRequest, *, user_id: UUID
    ) -> dict:
        context = await ClientService(self.db).build_client_context(organization, data.client_id)
        pack = await get_orchestrator().generate_creatives(
            context,
            CreativeRequest(
                platform=data.platform,
                format=data.format,
                objective=data.objective,
                topic=data.topic,
                count=data.count,
            ),
        )
        variations = [c.model_dump() for c in pack.concepts[: data.count]]
        action = await ActionService(self.db).create(
            organization.id,
            AIActionCreate(
                action_type=AIActionType.generate_creative_variations,
                client_id=data.client_id,
                agent="CreativeAgent",
                platform=data.platform,
                description=f"Generate {len(variations)} creative variations",
                reason="Variation engine request",
                payload={"variations": variations},
                priority=Priority.medium,
            ),
            user_id=user_id,
        )
        return {"action_id": str(action.id), "variations": variations, "status": action.status.value}

    async def creative_library(
        self,
        organization_id: UUID,
        *,
        client_id: UUID | None = None,
        asset_type: str | None = None,
        status: str | None = None,
    ) -> list[CreativeAssetOut]:
        stmt = (
            select(CreativeAsset)
            .where(CreativeAsset.organization_id == organization_id)
            .order_by(CreativeAsset.created_at.desc())
            .limit(200)
        )
        if client_id:
            stmt = stmt.where(CreativeAsset.client_id == client_id)
        if asset_type:
            stmt = stmt.where(CreativeAsset.asset_type == asset_type)
        if status:
            stmt = stmt.where(CreativeAsset.status == status)
        rows = (await self.db.execute(stmt)).scalars().all()
        return [CreativeAssetOut.model_validate(r) for r in rows]

    def _mark(self, run: AutopilotRun, key: str, status: str, detail: str | None = None) -> None:
        steps = deepcopy(list(run.steps or []))
        for step in steps:
            if step.get("key") == key:
                step["status"] = status
                if detail is not None:
                    step["detail"] = detail
                break
        run.steps = steps
        flag_modified(run, "steps")
