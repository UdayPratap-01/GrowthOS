from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.orchestrator import get_orchestrator
from app.core.deps import AuthContext, get_current_auth
from app.db.session import get_db
from app.models.ai_ops import AIConversation
from app.schemas.autopilot import AssistantCommandResult
from app.services.client_service import ClientService
from app.services.command_service import CommandService

router = APIRouter(prefix="/clients/{client_id}/assistant", tags=["assistant"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    structured: bool = True


class ChatResponse(BaseModel):
    reply: str
    conversation_id: UUID
    actions: list = Field(default_factory=list)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    client_id: UUID,
    data: ChatRequest,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    if data.structured:
        result: AssistantCommandResult = await CommandService(db).handle(
            auth.organization, client_id, data.message, user_id=auth.user.id
        )
        reply = result.reply
        actions = [a.model_dump(mode="json") for a in result.actions]
    else:
        context = await ClientService(db).build_client_context(auth.organization, client_id)
        reply = await get_orchestrator().chat(context, data.message)
        actions = []

    conversation = AIConversation(
        organization_id=auth.organization_id,
        client_id=client_id,
        agent="orchestrator",
        messages=[
            {"role": "user", "content": data.message},
            {"role": "assistant", "content": reply},
            {"role": "system", "content": f"structured_actions={len(actions)}"},
        ],
    )
    db.add(conversation)
    await db.flush()
    await db.refresh(conversation)
    return ChatResponse(reply=reply, conversation_id=conversation.id, actions=actions)
