from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.orchestrator import get_orchestrator
from app.models.marketing import ContentCalendar, SocialPost
from app.models.organization import Organization
from app.schemas.content import CalendarCreate, CalendarOut, ContentGenerateRequest, ContentGenerated, ContentSaveRequest, SocialPostOut
from app.services.client_service import ClientService


class ContentService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.clients = ClientService(db)
        self.orchestrator = get_orchestrator()

    async def generate(self, organization: Organization, client_id: UUID, request: ContentGenerateRequest) -> ContentGenerated:
        context = await self.clients.build_client_context(organization, client_id)
        return await self.orchestrator.generate_content(context, request)

    async def save(self, organization_id: UUID, client_id: UUID, data: ContentSaveRequest) -> SocialPostOut:
        await self.clients.get_client(organization_id, client_id)
        post = SocialPost(
            organization_id=organization_id,
            client_id=client_id,
            platform=data.platform,
            content_type=data.content_type,
            hook=data.hook,
            main_copy=data.main_copy,
            cta=data.cta,
            visual_concept=data.visual_concept,
            video_concept=data.video_concept,
            hashtags=data.hashtags,
            status=data.status,
        )
        self.db.add(post)
        await self.db.flush()
        await self.db.refresh(post)
        return SocialPostOut.model_validate(post)

    async def list_posts(self, organization_id: UUID, client_id: UUID) -> list[SocialPostOut]:
        result = await self.db.execute(
            select(SocialPost)
            .where(SocialPost.organization_id == organization_id, SocialPost.client_id == client_id)
            .order_by(SocialPost.created_at.desc())
        )
        return [SocialPostOut.model_validate(p) for p in result.scalars().all()]

    async def create_calendar_item(self, organization_id: UUID, client_id: UUID, data: CalendarCreate) -> CalendarOut:
        await self.clients.get_client(organization_id, client_id)
        item = ContentCalendar(organization_id=organization_id, client_id=client_id, **data.model_dump())
        self.db.add(item)
        await self.db.flush()
        await self.db.refresh(item)
        return CalendarOut.model_validate(item)

    async def list_calendar(self, organization_id: UUID, client_id: UUID) -> list[CalendarOut]:
        result = await self.db.execute(
            select(ContentCalendar)
            .where(ContentCalendar.organization_id == organization_id, ContentCalendar.client_id == client_id)
            .order_by(ContentCalendar.scheduled_for.asc().nulls_last())
        )
        return [CalendarOut.model_validate(i) for i in result.scalars().all()]
