from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from app.ai.providers.base import AIProvider, AIResponse, Message


class MockAIProvider(AIProvider):
    name = "mock"

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        temperature: float = 0.4,
    ) -> AIResponse:
        prompt = " ".join(m.content for m in messages).lower()
        payload: dict[str, Any]

        if schema and schema.__name__ == "StrategyGenerated":
            payload = {
                "title": "30-Day Growth Acceleration Plan",
                "current_situation": "Performance is mixed. Available demo analytics show rising CPL on paid social while organic engagement is stable.",
                "what_is_happening": "Lead volume is acceptable but efficiency is declining on Meta prospecting. Content output is inconsistent week to week.",
                "key_problems": [
                    "CPL pressure on paid acquisition",
                    "Creative fatigue signals on top ads",
                    "Weak mid-funnel nurture cadence",
                ],
                "opportunities": [
                    "Refresh creative with 3 new angles",
                    "Shift budget toward higher-intent retargeting",
                    "Launch weekly authority content on LinkedIn",
                ],
                "strategy_summary": "Stabilize acquisition efficiency, then expand demand-gen with a tighter content system and clearer lead follow-up.",
                "actions": [
                    {
                        "action": "Audit top Meta campaigns and pause creatives with CTR below account median",
                        "channel": "Meta Ads",
                        "objective": "Reduce wasted spend",
                        "priority": "high",
                        "estimated_effort": "medium",
                        "expected_outcome": "Lower blended CPL within 14 days",
                        "required_assets": ["Campaign export", "Creative matrix"],
                        "deadline": None,
                    },
                    {
                        "action": "Produce 3 Reel concepts addressing primary customer objection",
                        "channel": "Instagram",
                        "objective": "Improve organic reach and warm traffic",
                        "priority": "high",
                        "estimated_effort": "medium",
                        "expected_outcome": "More qualified inbound conversations",
                        "required_assets": ["Brand voice guide", "Offer sheet"],
                        "deadline": None,
                    },
                    {
                        "action": "Implement 48-hour lead response SLA in CRM",
                        "channel": "CRM",
                        "objective": "Increase conversion rate",
                        "priority": "medium",
                        "estimated_effort": "low",
                        "expected_outcome": "Higher meeting-booked rate from new leads",
                        "required_assets": ["Lead stage definitions"],
                        "deadline": None,
                    },
                ],
            }
        elif schema and schema.__name__ == "ContentGenerated":
            platform = "social"
            if "linkedin" in prompt:
                platform = "LinkedIn"
            elif "instagram" in prompt or "reel" in prompt:
                platform = "Instagram"
            payload = {
                "hook": "Most brands post more. Growth brands post with intent.",
                "main_copy": (
                    f"Here's a {platform} concept built around your topic. Lead with a sharp tension, "
                    "support it with one proof point from the offer, and close with a clear next step. "
                    "Keep the brand voice consistent and avoid generic motivational filler."
                ),
                "cta": "Book a strategy call this week",
                "visual_concept": "Clean brand-forward frame with bold headline typography and one product/context image.",
                "video_concept": "0-2s hook text, 2-8s problem, 8-15s solution proof, 15-20s CTA.",
                "hashtags": ["#GrowthMarketing", "#ContentStrategy", "#DemandGen"],
            }
        elif schema and schema.__name__ == "LeadScoreExplanation":
            payload = {
                "score": 72,
                "reasons": [
                    "Lead includes email and campaign attribution",
                    "Source indicates paid or form-based capture",
                ],
                "based_on_available_data_only": True,
                "insufficient_data_note": "Score is based only on available CRM fields. Behavioral events unavailable.",
            }
        elif schema and schema.__name__ == "WeeklyReportDraft":
            payload = {
                "executive_summary": (
                    "Available period metrics show mixed efficiency. Lead volume is present while CPL/CTR movement "
                    "should guide creative and budget decisions. No fabricated KPIs are included."
                ),
                "key_metrics": [
                    "Use provided spend/leads/revenue totals only",
                    "Call out Insufficient data where series are empty",
                ],
                "growth": ["Channels with stable or improving lead contribution"],
                "declines": ["Efficiency pressure where CPL rose versus prior period"],
                "insights": [
                    "Prioritize creative refresh on weak CTR placements",
                    "Keep lead follow-up SLA tight for high-scoring CRM leads",
                ],
                "next_week_strategy": (
                    "Ship 3 new creatives, protect high-intent campaigns, and run one conversion-focused content batch."
                ),
                "insufficient_data": [],
            }
        elif schema and schema.__name__ == "AnalyticsInsight":
            payload = {
                "summary": "Interpretation uses only supplied metrics.",
                "findings": ["Review CPL and CTR deltas before scaling spend."],
                "insufficient_data": [],
            }
        elif schema and schema.__name__ == "CreativePack":
            payload = {
                "concepts": [
                    {
                        "headline": "Stop guessing. Start converting.",
                        "primary_text": "A brand-aligned concept using your offer and audience. Keep proof points factual.",
                        "cta": "Book a consult",
                        "visual_concept": "Bold headline over product/context imagery matching brand colors.",
                        "variation_notes": "Swap CTA emphasis for retargeting audiences.",
                    },
                    {
                        "headline": "Your next lead is waiting.",
                        "primary_text": "Speak to the primary pain point with one clear offer.",
                        "cta": "Get started",
                        "visual_concept": "Clean carousel: problem → solution → proof → CTA.",
                        "variation_notes": "Shorten for Stories.",
                    },
                    {
                        "headline": "Proof over promises.",
                        "primary_text": "Lead with available social proof; never invent metrics.",
                        "cta": "See how it works",
                        "visual_concept": "Testimonial-style frame with brand typography.",
                        "variation_notes": "Use as ad variation B.",
                    },
                ],
                "brand_alignment_notes": "Concepts use provided brand voice and offer context only.",
                "insufficient_data": [],
            }
        elif schema and schema.__name__ == "OptimizationPlan":
            payload = {
                "summary": "Optimization suggestions use only provided campaign/analytics fields.",
                "suggestions": [
                    {
                        "problem": "Efficiency pressure detected on paid acquisition (from available deltas).",
                        "evidence": ["Use supplied CPL/CTR deltas only", "No invented ROAS"],
                        "recommendation": "Create three new creative variations and reduce spend on the weakest creative.",
                        "priority": "high",
                        "expected_impact": "Potential CPL improvement — not guaranteed.",
                        "suggested_action_type": "CREATE_CREATIVE",
                        "platform": "meta",
                        "target_id": None,
                        "estimated_cost": None,
                    }
                ],
                "insufficient_data": [],
            }
        elif schema and schema.__name__ == "MonitoringReport":
            payload = {
                "overview": "Health scoring uses only campaign metrics present in context.",
                "health": [],
                "alerts": [],
                "insufficient_data": ["Detailed per-campaign live stats unavailable unless synced."],
            }
        elif schema and schema.__name__ == "AdsInsight":
            payload = {
                "summary": "Ads insights limited to supplied campaign metrics.",
                "recommendations": ["Refresh weak creatives", "Protect efficient campaigns"],
                "insufficient_data": [],
            }
        elif schema and schema.__name__ == "CampaignStructure":
            payload = {
                "name": "Lead Gen — 30 Day Push",
                "objective": "Generate Leads",
                "platforms": ["meta", "instagram"],
                "total_budget": 500,
                "duration_days": 30,
                "messaging_strategy": "Lead with offer clarity and one proof point; avoid invented metrics.",
                "audience_strategy": "Prospecting + retargeting split using provided audience description.",
                "creative_concepts": ["Hero offer", "Problem/solution", "UGC-style"],
                "image_prompts": [
                    "Brand hero with offer headline",
                    "Problem/solution split visual",
                    "Product close-up with CTA",
                ],
                "video_scripts": [
                    "Hook → problem → offer → CTA in 15s",
                    "UGC testimonial concept (script only)",
                ],
                "hooks": ["Stop scrolling if you want more leads", "Your next member starts here"],
                "headlines": ["Generate more qualified leads", "This month's offer"],
                "primary_texts": ["Clear offer + audience fit. No fabricated ROAS."],
                "ctas": ["Learn More", "Book Now", "Get Started"],
                "ad_sets": [
                    {
                        "name": "Prospecting — Broad",
                        "audience": "Interest + lookalike (plan only)",
                        "placement": "Feed + Reels",
                        "daily_budget_share": 0.6,
                        "optimization": "leads",
                    },
                    {
                        "name": "Retargeting — Warm",
                        "audience": "Site visitors / engagers",
                        "placement": "Feed",
                        "daily_budget_share": 0.4,
                        "optimization": "conversions",
                    },
                ],
                "ads": [
                    {
                        "name": "Hero Ad",
                        "headline": "Generate more qualified leads",
                        "primary_text": "Offer-led primary text using client brand voice.",
                        "cta": "Learn More",
                        "creative_type": "image",
                        "destination": None,
                    }
                ],
                "risks": ["Live create requires connected Meta/Google with write scopes"],
                "insufficient_data": [],
                "notes": "Plan only — no platform IDs invented.",
            }
        elif schema and schema.__name__ == "AutopilotPlan":
            payload = {
                "summary": "Controlled marketing autopilot plan for the selected client.",
                "steps": [
                    {"key": "analyze_client", "label": "Client analyzed", "status": "pending"},
                    {"key": "strategy", "label": "Strategy created", "status": "pending"},
                    {"key": "campaign", "label": "Campaign structure created", "status": "pending"},
                    {"key": "approval", "label": "Campaign awaiting approval", "status": "pending"},
                ],
                "blockers": ["IMAGE GENERATION NOT CONFIGURED", "VIDEO GENERATION NOT CONFIGURED"],
                "insufficient_data": [],
                "notes": "Never claim live publish without API confirmation.",
            }
        elif schema and schema.__name__ == "ImageCreativePack":
            payload = {
                "prompts": [
                    {
                        "style": "hero",
                        "prompt": "Premium brand hero image with clear offer headline space",
                        "headline_suggestion": "This month's offer",
                        "notes": "Prompt only",
                    },
                    {
                        "style": "offer",
                        "prompt": "Clean offer card visual matching brand colors",
                        "headline_suggestion": "Limited seats",
                        "notes": "Prompt only",
                    },
                    {
                        "style": "ugc",
                        "prompt": "UGC-style lifestyle frame, natural lighting",
                        "headline_suggestion": "Real results start here",
                        "notes": "Prompt only",
                    },
                ],
                "insufficient_data": [],
            }
        elif schema and schema.__name__ == "VideoPack":
            payload = {
                "concepts": [
                    {
                        "title": "15s Lead Reel",
                        "hook": "Want more leads this month?",
                        "script": "Hook, problem, offer, CTA. Script only — not a rendered video.",
                        "visual_notes": "Vertical 9:16, brand colors",
                        "cta": "Learn More",
                        "duration_seconds": 15,
                    },
                    {
                        "title": "UGC Testimonial Concept",
                        "hook": "Here's what changed for us",
                        "script": "Talking-head style concept with overlay text.",
                        "visual_notes": "Handheld UGC feel",
                        "cta": "Book Now",
                        "duration_seconds": 20,
                    },
                ],
                "insufficient_data": [],
            }
        elif schema and schema.__name__ == "CompetitorInsight":
            payload = {
                "observations": ["Competitor names from client profile only — no invented spend."],
                "opportunities": ["Differentiate on offer clarity and creative testing cadence."],
                "risks": ["Do not mirror competitor claims without proof."],
                "insufficient_data": ["Live competitor ad libraries not connected"],
                "data_label": "AI ESTIMATE",
            }
        elif "assistant" in prompt or "chat" in prompt:
            payload = {
                "reply": (
                    "Based on the selected client context and available metrics, prioritize creative refresh "
                    "on underperforming paid campaigns, keep a steady content cadence, and tighten lead follow-up. "
                    "Where metrics are missing, I will say 'Insufficient data.' rather than invent numbers."
                )
            }
        else:
            payload = {"message": "Mock AI response. Configure OPENAI_API_KEY or ANTHROPIC_API_KEY for live models."}

        content = json.dumps(payload)
        if schema:
            validated = schema.model_validate(payload)
            content = validated.model_dump_json()
        return AIResponse(content=content, raw=payload, provider=self.name)
