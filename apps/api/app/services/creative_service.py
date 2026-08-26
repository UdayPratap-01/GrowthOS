from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.creative_agent import CreativeRequest
from app.ai.orchestrator import get_orchestrator
from app.core.config import get_settings
from app.models.automation import CreativeAsset
from app.models.enums import AIActionType, Priority
from app.schemas.autopilot import (
    AIActionCreate,
    CreativeAssetOut,
    CreativeGenerateRequest,
    ImageGenerateRequest,
    VideoGenerateRequest,
)
from app.services.action_service import ActionService
from app.services.client_service import ClientService
from app.services.media_generation_service import MediaGenerationService
from app.storage import key_belongs_to_organization


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
                prompt=data.topic or concept.visual_concept,
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
            assets.append(self._asset_out(row))

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
                expected_impact="Ready for image/video generation",
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
        return [self._asset_out(r) for r in rows]

    async def generate_image(self, organization, data: ImageGenerateRequest, *, user_id: UUID) -> dict:
        media = MediaGenerationService(self.db)
        result = await media.enqueue_images(
            organization,
            client_id=data.client_id,
            prompt=data.prompt,
            aspect_ratio="1:1",
            quantity=1,
            platform=data.platform,
        )
        if result.get("job_id"):
            await ActionService(self.db).create(
                organization.id,
                AIActionCreate(
                    action_type=AIActionType.generate_image,
                    client_id=data.client_id,
                    agent="CreativeAgent",
                    platform=data.platform,
                    description=f"Image generation: {data.prompt[:120]}",
                    reason="Image generation request",
                    evidence=[{"job_id": result.get("job_id")}],
                    payload={"prompt": data.prompt, "job_id": result.get("job_id")},
                ),
                user_id=user_id,
            )
        return result

    async def generate_video(self, organization, data: VideoGenerateRequest, *, user_id: UUID) -> dict:
        media = MediaGenerationService(self.db)
        result = await media.enqueue_video(
            organization,
            client_id=data.client_id,
            prompt=data.prompt,
            platform=data.platform,
        )
        if result.get("job_id"):
            await ActionService(self.db).create(
                organization.id,
                AIActionCreate(
                    action_type=AIActionType.generate_video,
                    client_id=data.client_id,
                    agent="CreativeAgent",
                    platform=data.platform,
                    description=f"Video generation: {data.prompt[:120]}",
                    reason="Video generation request",
                    evidence=[{"job_id": result.get("job_id")}],
                    payload={"prompt": data.prompt, "job_id": result.get("job_id")},
                ),
                user_id=user_id,
            )
        return result

    async def get_video_job(self, organization_id: UUID, job_id: UUID) -> dict:
        return await MediaGenerationService(self.db).get_video_job(organization_id, job_id, poll=True)

    def _asset_out(self, row: CreativeAsset) -> CreativeAssetOut:
        data = CreativeAssetOut.model_validate(row)
        # Ownership is checked from the key rather than by probing storage: a
        # HEAD request per row would turn a list into one round-trip per asset.
        # The media endpoint confirms existence when the bytes are actually read.
        if row.status == "completed" and key_belongs_to_organization(
            row.storage_key, row.organization_id
        ):
            payload = data.model_dump()
            payload["url"] = f"/api/v1/creative/media/{row.id}"
            return CreativeAssetOut.model_validate(payload)
        return data
