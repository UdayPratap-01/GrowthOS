"""Natural language → structured AI actions (never direct execution)."""

from __future__ import annotations

import re
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.orchestrator import get_orchestrator
from app.models.enums import AIActionType, Priority, RiskLevel
from app.schemas.autopilot import AIActionCreate, AIActionOut, AssistantCommandResult
from app.services.action_service import ActionService
from app.services.client_service import ClientService


class CommandService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def handle(self, organization, client_id: UUID, message: str, *, user_id: UUID) -> AssistantCommandResult:
        context = await ClientService(self.db).build_client_context(organization, client_id)
        actions: list[AIActionOut] = []
        lower = message.lower()

        # Pattern-based structured command mapping (safe; no free-text execution)
        if "generate" in lower and ("reel" in lower or "instagram" in lower):
            count = self._extract_count(lower, default=3)
            actions.append(
                await ActionService(self.db).create(
                    organization.id,
                    AIActionCreate(
                        action_type=AIActionType.create_content,
                        client_id=client_id,
                        agent="ContentAgent",
                        platform="instagram",
                        description=f"Generate {count} Instagram Reel concepts",
                        reason=f"Assistant command: {message}",
                        evidence=[{"command": message}],
                        expected_impact="Draft content ready for approval",
                        priority=Priority.medium,
                        payload={"count": count, "content_type": "Reel"},
                    ),
                    user_id=user_id,
                )
            )
        elif "ad variation" in lower or ("meta" in lower and "ad" in lower):
            actions.append(
                await ActionService(self.db).create(
                    organization.id,
                    AIActionCreate(
                        action_type=AIActionType.create_creative,
                        client_id=client_id,
                        agent="CreativeAgent",
                        platform="meta",
                        description="Create Meta ad creative variations",
                        reason=f"Assistant command: {message}",
                        evidence=[{"command": message}],
                        expected_impact="Creative pack for review",
                        priority=Priority.high,
                        risk_level=RiskLevel.medium,
                        payload={"variations": 3},
                    ),
                    user_id=user_id,
                )
            )
        elif "schedule" in lower and "content" in lower:
            from datetime import datetime, timedelta, timezone

            when = (datetime.now(timezone.utc) + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
            actions.append(
                await ActionService(self.db).create(
                    organization.id,
                    AIActionCreate(
                        action_type=AIActionType.schedule_content,
                        client_id=client_id,
                        agent="SocialMediaAgent",
                        platform="instagram",
                        description="Schedule this week's content calendar drafts",
                        reason=f"Assistant command: {message}",
                        evidence=[{"command": message}],
                        expected_impact="Scheduled drafts pending approval/publish",
                        priority=Priority.medium,
                        payload={
                            "window": "this_week",
                            "scheduled_for": when.isoformat(),
                            "content": {"caption": "Scheduled draft from assistant command"},
                        },
                    ),
                    user_id=user_id,
                )
            )
        elif "create a campaign" in lower or "create campaign" in lower:
            budget = self._extract_budget(lower)
            actions.append(
                await ActionService(self.db).create(
                    organization.id,
                    AIActionCreate(
                        action_type=AIActionType.create_campaign,
                        client_id=client_id,
                        agent="AdsAgent",
                        platform="meta",
                        description="Propose campaign for current offer",
                        reason=f"Assistant command: {message}",
                        evidence=[{"command": message}],
                        expected_impact="Campaign proposal for approval — not live until platform confirms",
                        estimated_cost=budget or Decimal("50"),
                        priority=Priority.high,
                        risk_level=RiskLevel.high,
                        payload={"objective": "leads"},
                    ),
                    user_id=user_id,
                )
            )
        elif "pause" in lower and "cpl" in lower:
            threshold = self._extract_money(lower) or Decimal("500")
            actions.append(
                await ActionService(self.db).create(
                    organization.id,
                    AIActionCreate(
                        action_type=AIActionType.pause_campaign,
                        client_id=client_id,
                        agent="OptimizationAgent",
                        platform="meta",
                        description=f"Conditional pause if CPL exceeds {threshold}",
                        reason=f"Assistant command: {message}",
                        evidence=[{"command": message, "cpl_threshold": str(threshold)}],
                        expected_impact="Risk control rule recorded as structured action",
                        priority=Priority.high,
                        risk_level=RiskLevel.high,
                        payload={"condition": {"cpl_gt": str(threshold)}},
                    ),
                    user_id=user_id,
                )
            )
        elif "increase" in lower and "budget" in lower:
            pct = self._extract_pct(lower) or Decimal("15")
            actions.append(
                await ActionService(self.db).create(
                    organization.id,
                    AIActionCreate(
                        action_type=AIActionType.update_budget,
                        client_id=client_id,
                        agent="OptimizationAgent",
                        platform="meta",
                        description=f"Increase campaign budget by {pct}% if efficiency holds",
                        reason=f"Assistant command: {message}",
                        evidence=[{"command": message, "pct": str(pct)}],
                        expected_impact="Budget change requires approval and platform confirmation",
                        estimated_cost=Decimal("1"),
                        priority=Priority.high,
                        risk_level=RiskLevel.high,
                        payload={"budget_increase_pct": str(pct)},
                    ),
                    user_id=user_id,
                )
            )

        chat_reply = await get_orchestrator().chat(context, message)
        if actions:
            reply = (
                f"{chat_reply}\n\n"
                f"Created {len(actions)} structured action(s) for approval/execution. "
                "Nothing was published or mutated on live platforms from natural language alone."
            )
        else:
            reply = (
                f"{chat_reply}\n\n"
                "No structured execution action was inferred. "
                "Try commands like: create a campaign, generate 10 Instagram Reels, schedule this week's content."
            )
        return AssistantCommandResult(reply=reply, actions=actions)

    def _extract_count(self, text: str, default: int = 3) -> int:
        m = re.search(r"(\d+)\s+(instagram\s+)?reels?", text)
        if m:
            return max(1, min(int(m.group(1)), 20))
        m = re.search(r"generate\s+(\d+)", text)
        if m:
            return max(1, min(int(m.group(1)), 20))
        return default

    def _extract_budget(self, text: str) -> Decimal | None:
        m = re.search(r"(?:₹|rs\.?|inr|\$)?\s*([0-9]{2,7})", text)
        if m:
            return Decimal(m.group(1))
        return None

    def _extract_money(self, text: str) -> Decimal | None:
        m = re.search(r"(?:₹|rs\.?|\$)\s*([0-9]+(?:\.[0-9]+)?)", text)
        if m:
            return Decimal(m.group(1))
        m = re.search(r"exceeds\s+([0-9]+)", text)
        if m:
            return Decimal(m.group(1))
        return None

    def _extract_pct(self, text: str) -> Decimal | None:
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        if m:
            return Decimal(m.group(1))
        return None
