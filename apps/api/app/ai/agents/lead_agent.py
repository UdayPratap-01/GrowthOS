from pydantic import BaseModel

from app.ai.agents.base import BaseAgent
from app.ai.providers.base import Message
from app.schemas.client import ClientContext
from app.schemas.lead import LeadScoreExplanation


class LeadScoreRequest(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    source: str | None = None
    campaign: str | None = None
    ad: str | None = None
    status: str | None = None
    notes: str | None = None
    known_activities: list[str] = []


class LeadAgent(BaseAgent[LeadScoreRequest, LeadScoreExplanation]):
    name = "LeadAgent"
    output_schema = LeadScoreExplanation

    def build_messages(self, context: ClientContext, request: LeadScoreRequest) -> list[Message]:
        return [
            Message(
                role="system",
                content=(
                    "You are LeadAgent for GrowthOS AI. Score leads 0-100 using only provided fields/activities. "
                    "Never fabricate behavior. If data is thin, say the score is based only on available information."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Score this lead for client {context.business_name}.\n"
                    f"Lead JSON: {request.model_dump_json()}\n"
                    f"Available client KPIs: {context.kpis}\n"
                    f"Insufficient fields: {context.insufficient_data_fields}"
                ),
            ),
        ]

    def deterministic_score(self, request: LeadScoreRequest) -> LeadScoreExplanation:
        """Rule-based fallback that never invents behavioral events."""
        score = 35
        reasons: list[str] = []
        if request.email:
            score += 15
            reasons.append("Valid email present")
        if request.phone:
            score += 10
            reasons.append("Phone number present")
        if request.source:
            score += 10
            reasons.append(f"Source attributed: {request.source}")
        if request.campaign:
            score += 12
            reasons.append(f"Campaign attributed: {request.campaign}")
        if request.ad:
            score += 8
            reasons.append(f"Ad attributed: {request.ad}")
        if request.notes:
            score += 5
            reasons.append("Notes provide additional context")
        for activity in request.known_activities:
            score += 5
            reasons.append(f"Observed activity: {activity}")
        score = min(score, 100)
        if not reasons:
            reasons.append("Insufficient data for detailed scoring signals")
        return LeadScoreExplanation(
            score=score,
            reasons=reasons,
            based_on_available_data_only=True,
            insufficient_data_note="Score is based only on available CRM fields. Behavioral events unavailable."
            if not request.known_activities
            else None,
        )
