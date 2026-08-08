from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.creative_agent import CreativeRequest
from app.ai.orchestrator import get_orchestrator
from app.core.config import get_settings
from app.generation import get_image_provider, get_video_provider
from app.models.automation import CreativeAsset, ImageJob, VideoJob
from app.models.enums import AIActionType, JobStatus, Priority
from app.schemas.autopilot import (
    AIActionCreate,
    CreativeAssetOut,
    CreativeGenerateRequest,
    ImageGenerateRequest,
    VideoGenerateRequest,
)
from app.services.action_service import ActionService
from app.services.client_service import ClientService


class CreativeService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def generate_concepts(self, organization, data: CreativeGenerateRequest, *, user_id: UUID) -> list[CreativeAssetOut]:
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
        demo = bool(organization.demo_mode or get_settings().demo_mode)
        assets: list[CreativeAssetOut] = []
        for concept in pack.concepts[: data.count]:
            row = CreativeAsset(
                organization_id=organization.id,
                client_id=data.client_id,
                name=concept.headline[:255],
                asset_type="concept",
                platform=data.platform,
                prompt=data.topic,
                provider="creative_agent",
                status="draft",
                content=concept.model_dump(),
                meta={
                    "format": data.format,
                    "objective": data.objective,
                    "brand_alignment_notes": pack.brand_alignment_notes,
                    "insufficient_data": pack.insufficient_data,
                },
                data_source="demo" if demo else "live",
            )
            self.db.add(row)
            await self.db.flush()
            await self.db.refresh(row)
            assets.append(CreativeAssetOut.model_validate(row))

        await ActionService(self.db).create(
            organization.id,
            AIActionCreate(
                action_type=AIActionType.create_creative,
                client_id=data.client_id,
                agent="CreativeAgent",
                platform=data.platform,
                description=f"Generated {len(assets)} creative concepts for {data.platform}",
                reason="Creative automation request",
                evidence=[{"count": len(assets), "format": data.format}],
                expected_impact="Ready for approval / image generation",
                priority=Priority.medium,
                payload={"creative_asset_ids": [str(a.id) for a in assets]},
            ),
            user_id=user_id,
        )
        return assets

    async def list_assets(self, organization_id: UUID, client_id: UUID | None = None) -> list[CreativeAssetOut]:
        stmt = (
            select(CreativeAsset)
            .where(CreativeAsset.organization_id == organization_id)
            .order_by(CreativeAsset.created_at.desc())
            .limit(100)
        )
        if client_id:
            stmt = stmt.where(CreativeAsset.client_id == client_id)
        rows = (await self.db.execute(stmt)).scalars().all()
        return [CreativeAssetOut.model_validate(r) for r in rows]

    async def generate_image(self, organization, data: ImageGenerateRequest, *, user_id: UUID) -> dict:
        provider = get_image_provider()
        job = ImageJob(
            organization_id=organization.id,
            client_id=data.client_id,
            provider=provider.name,
            prompt=data.prompt,
            status=JobStatus.queued,
        )
        self.db.add(job)
        await self.db.flush()

        result = await provider.generate_image(prompt=data.prompt)
        job.status = JobStatus.completed if result.success else JobStatus.failed
        job.result = {"message": result.message, "assets": result.assets, "demo": result.demo}
        job.error = result.error
        await self.db.flush()

        action = await ActionService(self.db).create(
            organization.id,
            AIActionCreate(
                action_type=AIActionType.generate_image,
                client_id=data.client_id,
                agent="CreativeAgent",
                platform=data.platform,
                description=f"Image generation: {data.prompt[:120]}",
                reason="Image generation request",
                evidence=[{"job_id": str(job.id), "provider": provider.name}],
                payload={"prompt": data.prompt, "job_id": str(job.id)},
            ),
            user_id=user_id,
        )
        return {
            "job_id": str(job.id),
            "status": job.status.value,
            "message": result.message,
            "demo": result.demo,
            "action_id": str(action.id),
            "error": result.error,
        }

    async def generate_video(self, organization, data: VideoGenerateRequest, *, user_id: UUID) -> dict:
        provider = get_video_provider()
        job = VideoJob(
            organization_id=organization.id,
            client_id=data.client_id,
            provider=provider.name,
            prompt=data.prompt,
            status=JobStatus.queued,
        )
        self.db.add(job)
        await self.db.flush()
        result = await provider.generate_video(prompt=data.prompt)
        job.status = JobStatus.completed if result.success else JobStatus.failed
        job.result = {"message": result.message, "assets": result.assets, "demo": result.demo}
        job.error = result.error
        await self.db.flush()
        action = await ActionService(self.db).create(
            organization.id,
            AIActionCreate(
                action_type=AIActionType.generate_video,
                client_id=data.client_id,
                agent="CreativeAgent",
                platform=data.platform,
                description=f"Video generation: {data.prompt[:120]}",
                reason="Video generation request",
                evidence=[{"job_id": str(job.id), "provider": provider.name}],
                payload={"prompt": data.prompt, "job_id": str(job.id)},
            ),
            user_id=user_id,
        )
        return {
            "job_id": str(job.id),
            "status": job.status.value,
            "message": result.message,
            "demo": result.demo,
            "action_id": str(action.id),
            "error": result.error,
        }

    async def get_video_job(self, organization_id: UUID, job_id: UUID) -> dict:
        job = await self.db.scalar(
            select(VideoJob).where(VideoJob.id == job_id, VideoJob.organization_id == organization_id)
        )
        if not job:
            return {"error": "NOT_FOUND"}
        return {
            "id": str(job.id),
            "status": job.status.value,
            "provider": job.provider,
            "result": job.result,
            "error": job.error,
        }
