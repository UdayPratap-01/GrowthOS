"""Extensible registry of AI action types and default risk/approval rules."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import AIActionType, RiskLevel

FINANCIAL_ACTIONS = {
    AIActionType.create_campaign,
    AIActionType.create_ad_set,
    AIActionType.create_ad,
    AIActionType.update_budget,
    AIActionType.pause_campaign,
    AIActionType.resume_campaign,
    AIActionType.optimize_campaign,
}

PUBLISH_ACTIONS = {
    AIActionType.publish_content,
    AIActionType.schedule_content,
}

CAMPAIGN_CREATE_ACTIONS = {
    AIActionType.create_campaign,
    AIActionType.create_ad_set,
    AIActionType.create_ad,
}


@dataclass(frozen=True)
class ActionSpec:
    action_type: AIActionType
    default_risk: RiskLevel
    requires_platform: bool = False
    reversible: bool = False


ACTION_REGISTRY: dict[AIActionType, ActionSpec] = {
    AIActionType.create_campaign: ActionSpec(AIActionType.create_campaign, RiskLevel.high, True, False),
    AIActionType.create_ad_set: ActionSpec(AIActionType.create_ad_set, RiskLevel.high, True, False),
    AIActionType.create_ad: ActionSpec(AIActionType.create_ad, RiskLevel.high, True, False),
    AIActionType.update_campaign: ActionSpec(AIActionType.update_campaign, RiskLevel.medium, True, True),
    AIActionType.update_budget: ActionSpec(AIActionType.update_budget, RiskLevel.high, True, True),
    AIActionType.pause_campaign: ActionSpec(AIActionType.pause_campaign, RiskLevel.medium, True, True),
    AIActionType.resume_campaign: ActionSpec(AIActionType.resume_campaign, RiskLevel.medium, True, True),
    AIActionType.create_creative: ActionSpec(AIActionType.create_creative, RiskLevel.low, False, False),
    AIActionType.generate_image: ActionSpec(AIActionType.generate_image, RiskLevel.low, False, False),
    AIActionType.generate_video: ActionSpec(AIActionType.generate_video, RiskLevel.low, False, False),
    AIActionType.create_content: ActionSpec(AIActionType.create_content, RiskLevel.low, False, False),
    AIActionType.schedule_content: ActionSpec(AIActionType.schedule_content, RiskLevel.medium, True, True),
    AIActionType.publish_content: ActionSpec(AIActionType.publish_content, RiskLevel.high, True, False),
    AIActionType.update_content: ActionSpec(AIActionType.update_content, RiskLevel.low, False, True),
    AIActionType.generate_report: ActionSpec(AIActionType.generate_report, RiskLevel.low, False, False),
    AIActionType.generate_recommendation: ActionSpec(AIActionType.generate_recommendation, RiskLevel.low, False, False),
    AIActionType.create_lead_action: ActionSpec(AIActionType.create_lead_action, RiskLevel.low, False, False),
    AIActionType.send_notification: ActionSpec(AIActionType.send_notification, RiskLevel.low, False, False),
    AIActionType.optimize_campaign: ActionSpec(AIActionType.optimize_campaign, RiskLevel.medium, True, True),
    AIActionType.generate_creative_variations: ActionSpec(
        AIActionType.generate_creative_variations, RiskLevel.low, False, False
    ),
}


def get_action_spec(action_type: AIActionType) -> ActionSpec:
    return ACTION_REGISTRY[action_type]


def list_action_types() -> list[str]:
    return [a.value for a in AIActionType]
