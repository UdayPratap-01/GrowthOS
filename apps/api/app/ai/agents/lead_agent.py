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
        """
        Transparent rule-based score over CRM fields that are actually present.

        This is not an LLM call and must never be presented as one. It scores
        only recorded fields and explicitly declares behavioural signals the
        product does not collect (page visits, email opens, form behaviour)
        as limitations rather than inventing them.
        """
        score = 35
        reasons: list[str] = ["Base score for a recorded lead: 35"]
        evidence: list[str] = []

        if request.email:
            score += 15
            reasons.append("Valid email present (+15)")
            evidence.append(f"email={request.email}")
        if request.phone:
            score += 10
            reasons.append("Phone number present (+10)")
            evidence.append(f"phone={request.phone}")
        if request.source:
            score += 10
            reasons.append(f"Source attributed: {request.source} (+10)")
            evidence.append(f"source={request.source}")
        if request.campaign:
            score += 12
            reasons.append(f"Campaign attributed: {request.campaign} (+12)")
            evidence.append(f"campaign={request.campaign}")
        if request.ad:
            score += 8
            reasons.append(f"Ad attributed: {request.ad} (+8)")
            evidence.append(f"ad={request.ad}")
        if request.notes:
            score += 5
            reasons.append("Notes provide additional context (+5)")
            evidence.append("notes=present")
        for activity in request.known_activities:
            score += 5
            reasons.append(f"Recorded activity: {activity} (+5)")
            evidence.append(f"activity={activity}")

        score = min(score, 100)

        data_limitations: list[str] = []
        if not request.email:
            data_limitations.append("No email on record. Insufficient data.")
        if not request.phone:
            data_limitations.append("No phone number on record. Insufficient data.")
        if not (request.source or request.campaign or request.ad):
            data_limitations.append("No campaign or ad attribution on record. Insufficient data.")
        if not request.known_activities:
            data_limitations.append(
                "No recorded lead activities. Website visits, pricing-page views, email opens and "
                "form behaviour are not tracked by this system and were not used. Insufficient data."
            )
        if not evidence:
            reasons.append("No scoring signals available beyond the base score.")

        return LeadScoreExplanation(
            score=score,
            method="deterministic_rules",
            method_label="Deterministic rule-based scoring",
            reasons=reasons,
            evidence=evidence or ["Insufficient data."],
            data_limitations=data_limitations,
            based_on_available_data_only=True,
            insufficient_data_note=" ".join(data_limitations) or None,
        )
