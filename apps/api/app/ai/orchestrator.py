from __future__ import annotations

from app.ai.agents.ads_agent import AdsAgent, AdsInsight, AdsInsightRequest
from app.ai.agents.analytics_agent import AnalyticsAgent, AnalyticsInsight, AnalyticsInsightRequest
from app.ai.agents.autopilot_agent import AutopilotAgent, AutopilotPlan, AutopilotPlanRequest
from app.ai.agents.campaign_builder_agent import CampaignBuilderAgent, CampaignBuilderRequest
from app.ai.agents.campaign_planner_agent import (
    CampaignPlanRequest,
    CampaignPlannerAgent,
    CampaignStructure,
)
from app.ai.agents.campaign_strategy_agent import CampaignStrategyAgent, CampaignStrategyRequest
from app.ai.agents.competitor_agent import CompetitorAgent, CompetitorInsight, CompetitorInsightRequest
from app.ai.agents.content_agent import ContentAgent
from app.ai.agents.copy_agent import CopyAgent, CopyRequest
from app.ai.agents.creative_agent import CreativeAgent, CreativePack, CreativeRequest
from app.ai.agents.creative_brief_agent import CreativeBriefAgent, CreativeBriefRequest
from app.ai.agents.creative_concept_agent import CreativeConceptAgent, CreativeConceptRequest
from app.ai.agents.image_creative_agent import ImageCreativeAgent, ImageCreativePack, ImageCreativeRequest
from app.ai.agents.lead_agent import LeadAgent, LeadScoreRequest
from app.ai.agents.monitoring_agent import MonitoringAgent, MonitoringReport, MonitoringRequest
from app.ai.agents.optimization_agent import OptimizationAgent, OptimizationPlan, OptimizationRequest
from app.ai.agents.report_agent import ReportAgent, ReportRequest, WeeklyReportDraft
from app.ai.agents.strategy_agent import StrategyAgent, StrategyRequest
from app.ai.agents.variation_agent import VariationAgent, VariationRequest
from app.ai.agents.video_agent import VideoAgent, VideoAgentRequest, VideoPack
from app.ai.providers.base import AIProvider, Message
from app.ai.providers.factory import get_ai_provider
from app.schemas.campaign_generation import (
    CampaignBlueprint,
    CampaignBriefDraft,
    CampaignStrategy,
    CopyConceptPack,
    CreativeConceptPack,
    VariationPack,
)
from app.schemas.client import ClientContext
from app.schemas.content import ContentGenerateRequest, ContentGenerated
from app.schemas.lead import LeadScoreExplanation
from app.schemas.strategy import StrategyGenerated


class AIOrchestrator:
    """Coordinates modular agents. No giant monolithic prompt."""

    def __init__(self, provider: AIProvider | None = None) -> None:
        self.provider = provider or get_ai_provider()
        self.strategy_agent = StrategyAgent(self.provider)
        self.content_agent = ContentAgent(self.provider)
        self.lead_agent = LeadAgent(self.provider)
        self.analytics_agent = AnalyticsAgent(self.provider)
        self.ads_agent = AdsAgent(self.provider)
        self.report_agent = ReportAgent(self.provider)
        self.creative_agent = CreativeAgent(self.provider)
        self.optimization_agent = OptimizationAgent(self.provider)
        self.monitoring_agent = MonitoringAgent(self.provider)
        self.campaign_planner_agent = CampaignPlannerAgent(self.provider)
        self.autopilot_agent = AutopilotAgent(self.provider)
        self.image_creative_agent = ImageCreativeAgent(self.provider)
        self.video_agent = VideoAgent(self.provider)
        self.competitor_agent = CompetitorAgent(self.provider)
        # P2-A creative engine. Separate agents rather than one prompt: each
        # stage validates its own schema, and a failure names the stage that
        # failed instead of collapsing the whole campaign into one error.
        self.campaign_strategy_agent = CampaignStrategyAgent(self.provider)
        self.creative_brief_agent = CreativeBriefAgent(self.provider)
        self.copy_agent = CopyAgent(self.provider)
        self.creative_concept_agent = CreativeConceptAgent(self.provider)
        self.variation_agent = VariationAgent(self.provider)
        self.campaign_builder_agent = CampaignBuilderAgent(self.provider)

    async def generate_strategy(self, context: ClientContext, title: str | None = None) -> StrategyGenerated:
        return await self.strategy_agent.run(context, StrategyRequest(title=title))

    async def generate_content(self, context: ClientContext, request: ContentGenerateRequest) -> ContentGenerated:
        return await self.content_agent.run(context, request)

    def score_lead_deterministic(
        self, context: ClientContext, request: LeadScoreRequest
    ) -> LeadScoreExplanation:
        """
        Score a lead with the deterministic rule engine.

        Deliberately not an LLM call: the system holds no behavioural signals to
        reason over, so a model could only speculate. Naming it `score_lead`
        alongside the other agent methods implied AI scoring that never ran.
        """
        return self.lead_agent.deterministic_score(request)

    async def analytics_insight(self, context: ClientContext, question: str) -> AnalyticsInsight:
        return await self.analytics_agent.run(context, AnalyticsInsightRequest(question=question))

    async def ads_insight(self, context: ClientContext, focus: str = "efficiency") -> AdsInsight:
        return await self.ads_agent.run(context, AdsInsightRequest(focus=focus))

    async def weekly_report(self, context: ClientContext, period_label: str = "This week") -> WeeklyReportDraft:
        return await self.report_agent.run(context, ReportRequest(period_label=period_label))

    async def generate_creatives(self, context: ClientContext, request: CreativeRequest) -> CreativePack:
        return await self.creative_agent.run(context, request)

    async def optimize(
        self, context: ClientContext, *, analytics_summary: dict, campaigns: list[dict], focus: str = "cpl_and_creative_fatigue"
    ) -> OptimizationPlan:
        return await self.optimization_agent.run(
            context,
            OptimizationRequest(focus=focus, analytics_summary=analytics_summary, campaigns=campaigns),
        )

    async def monitor(
        self, context: ClientContext, *, analytics_summary: dict, campaigns: list[dict]
    ) -> MonitoringReport:
        return await self.monitoring_agent.run(
            context, MonitoringRequest(analytics_summary=analytics_summary, campaigns=campaigns)
        )

    async def plan_campaign(self, context: ClientContext, request: CampaignPlanRequest) -> CampaignStructure:
        return await self.campaign_planner_agent.run(context, request)

    async def plan_autopilot(self, context: ClientContext, request: AutopilotPlanRequest) -> AutopilotPlan:
        return await self.autopilot_agent.run(context, request)

    async def image_creatives(self, context: ClientContext, request: ImageCreativeRequest) -> ImageCreativePack:
        return await self.image_creative_agent.run(context, request)

    async def video_concepts(self, context: ClientContext, request: VideoAgentRequest) -> VideoPack:
        return await self.video_agent.run(context, request)

    async def competitor_insight(
        self, context: ClientContext, request: CompetitorInsightRequest | None = None
    ) -> CompetitorInsight:
        return await self.competitor_agent.run(context, request or CompetitorInsightRequest())

    # ------------------------------------------------------------------
    # P2-A campaign generation stages
    # ------------------------------------------------------------------

    async def campaign_strategy(
        self, context: ClientContext, request: CampaignStrategyRequest
    ) -> CampaignStrategy:
        return await self.campaign_strategy_agent.run(context, request)

    async def creative_brief(
        self, context: ClientContext, request: CreativeBriefRequest
    ) -> CampaignBriefDraft:
        return await self.creative_brief_agent.run(context, request)

    async def campaign_copy(self, context: ClientContext, request: CopyRequest) -> CopyConceptPack:
        return await self.copy_agent.run(context, request)

    async def creative_concepts(
        self, context: ClientContext, request: CreativeConceptRequest
    ) -> CreativeConceptPack:
        return await self.creative_concept_agent.run(context, request)

    async def creative_variations(
        self, context: ClientContext, request: VariationRequest
    ) -> VariationPack:
        return await self.variation_agent.run(context, request)

    async def build_campaign_structure(
        self, context: ClientContext, request: CampaignBuilderRequest
    ) -> CampaignBlueprint:
        return await self.campaign_builder_agent.run(context, request)

    async def chat(self, context: ClientContext, question: str) -> str:
        messages = [
            Message(
                role="system",
                content=(
                    "You are the GrowthOS AI Assistant. Stay client-aware. "
                    "Use provided context/metrics only. Never invent metrics. "
                    "If unknown, say Insufficient data. "
                    "For execution requests, note that structured actions are created separately — never claim live execution here."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Client context: {context.model_dump_json()}\n"
                    f"Question: {question}\n"
                    "assistant chat"
                ),
            ),
        ]
        response = await self.provider.complete(messages)
        try:
            import json

            data = json.loads(response.content)
            return data.get("reply") or response.content
        except Exception:
            return response.content


def get_orchestrator() -> AIOrchestrator:
    return AIOrchestrator()
