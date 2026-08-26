from __future__ import annotations

import ast
import json
import re
from typing import Any

from pydantic import BaseModel

from app.ai.providers.base import AIProvider, AIResponse, Message

#: Concept references named in the prompt, in the order they appear. The mock
#: honours them rather than hard-coding A/B/C so a request for two or five
#: concepts still produces a self-consistent package that downstream stages can
#: join on.
_REFERENCE_LIST = re.compile(r"in order:\s*([A-Z](?:\s*,\s*[A-Z])*)")
_CONCEPT_ID = re.compile(r"'concept_id':\s*'([A-Za-z0-9]+)'")
_ASPECT_RATIOS = re.compile(r"Aspect ratios to design for:\s*(\[[^\]]*\])")


def _references_from_prompt(raw: str, *, default: tuple[str, ...] = ("A", "B", "C")) -> list[str]:
    listed = _REFERENCE_LIST.search(raw)
    if listed:
        return [part.strip() for part in listed.group(1).split(",") if part.strip()]
    found = list(dict.fromkeys(_CONCEPT_ID.findall(raw)))
    return found or list(default)


def _aspect_ratios_from_prompt(raw: str) -> list[str] | None:
    """Echo the ratios the concept agent was asked to design for, when present."""
    match = _ASPECT_RATIOS.search(raw or "")
    if not match:
        return None
    try:
        value = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return None
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        return list(value)
    return None


class MockAIProvider(AIProvider):
    name = "mock"

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        temperature: float = 0.4,
    ) -> AIResponse:
        raw_prompt = " ".join(m.content for m in messages)
        prompt = raw_prompt.lower()
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
        elif schema and schema.__name__ == "CampaignStrategy":
            payload = _campaign_strategy_payload()
        elif schema and schema.__name__ == "CampaignBriefDraft":
            payload = _campaign_brief_payload()
        elif schema and schema.__name__ == "CopyConceptPack":
            payload = _copy_pack_payload(_references_from_prompt(raw_prompt))
        elif schema and schema.__name__ == "CreativeConceptPack":
            payload = _concept_pack_payload(
                _references_from_prompt(raw_prompt),
                needs_video="video_prompt`: a single" in raw_prompt,
                aspect_ratios=_aspect_ratios_from_prompt(raw_prompt),
            )
        elif schema and schema.__name__ == "VariationPack":
            payload = _variation_pack_payload(raw_prompt)
        elif schema and schema.__name__ == "CampaignBlueprint":
            payload = _blueprint_payload(_references_from_prompt(raw_prompt))
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


# ---------------------------------------------------------------------------
# P2-A campaign engine payloads
#
# These stand in for a real provider in development and in the test suite, so
# they model the same discipline the prompts demand: no invented performance
# numbers, explicit data limitations, and concepts that are genuinely different
# hypotheses rather than one idea reworded. A mock that cheated on distinctness
# would let a real regression pass.
# ---------------------------------------------------------------------------


def _campaign_strategy_payload() -> dict[str, Any]:
    return {
        "current_situation": (
            "The client profile describes the business, offer and audience. No connected "
            "advertising analytics are present in this context, so current paid "
            "performance is Insufficient data."
        ),
        "problem": (
            "Demand is not being captured systematically: there is no evidence in the "
            "context of a repeatable acquisition campaign for the primary offer."
        ),
        "opportunity": (
            "Run one focused campaign against the primary offer with three distinct "
            "creative hypotheses, so the first weeks produce a readable result rather "
            "than an untestable spread."
        ),
        "target_audience": (
            "The audience described in the client profile, narrowed to those with an "
            "immediate need for the primary service."
        ),
        "positioning": "The specific, credible alternative to the obvious default choice.",
        "core_message": "One clear promise tied to the offer, supported by what the client can actually substantiate.",
        "offer_strategy": (
            "Lead with the supplied offer unchanged. No discount, price or guarantee is "
            "introduced that was not provided."
        ),
        "creative_strategy": (
            "Three angles across different families — problem-agitate, authority and "
            "direct-offer — each with imagery grounded in the client's real service "
            "context rather than stock business scenes."
        ),
        "channel_strategy": (
            "Concentrate budget on the selected platform and its highest-intent "
            "placements before expanding. Expansion is a later decision that needs data "
            "this account does not yet have."
        ),
        "campaign_objective": "As selected in the request.",
        "success_metrics": [
            "Cost per lead",
            "Lead volume",
            "Lead quality rate once qualification is recorded",
        ],
        "risks": [
            "No historical benchmark exists, so early cost figures cannot be judged good or bad.",
            "Creative fatigue is likely if only one angle is funded.",
            "Lead quality cannot be assessed until qualification outcomes are recorded.",
        ],
        "data_limitations": [
            "No historical campaign performance available for this client.",
            "No conversion tracking data present in the context.",
            "Competitor performance is not available; competitor entries are names only.",
        ],
        "evidence": [
            {
                "claim": "The audience definition comes from the client record, not an assumption.",
                "source": "client.target_audience",
                "value": None,
            },
            {
                "claim": "Budget guidance is the figure supplied in the request.",
                "source": "request.total_budget",
                "value": None,
            },
        ],
    }


def _campaign_brief_payload() -> dict[str, Any]:
    return {
        "campaign_name": "Primary Offer — Lead Capture",
        "offer": "The primary offer described in the client profile.",
        "audience": "Decision-makers matching the client's stated target audience.",
        "pain_points": [
            "Cannot tell which marketing spend is producing enquiries",
            "Enquiries arrive inconsistently, making planning hard",
            "Previous attempts produced volume without qualification",
        ],
        "value_proposition": "A specific, deliverable outcome the client can stand behind.",
        "messaging_angle": "Name the cost of the current situation, then present the offer as the resolution.",
        "tone": "Direct, credible, free of hype",
        "brand_constraints": [
            "No performance claims that cannot be substantiated",
            "No competitor names in creative",
            "Keep to the brand voice recorded on the client",
        ],
        "success_metrics": ["Cost per lead", "Lead volume", "Qualified lead rate"],
        "creative_direction": (
            "Real service context, natural light, one clear subject, deliberate space for "
            "a short headline."
        ),
        "cta": "Book a call",
        "data_limitations": ["No historical creative performance to inform which angle to favour."],
    }


#: Distinct hypotheses, one per reference. Cycled if more concepts are requested
#: than there are entries.
_ANGLE_SET = (
    {
        "angle": "problem-agitate — the cost of not knowing which spend works",
        "hook": "You are paying for marketing you cannot measure.",
        "primary_text": (
            "Most of the budget goes out before anyone can say which part produced an "
            "enquiry. We fix the measuring first, then scale what already works."
        ),
        "headline": "Know what actually works",
        "description": "Start with clarity",
        "cta": "Book a call",
        "hypothesis": (
            "Tests whether loss-framing around wasted spend outperforms positive framing. "
            "A win means this audience is further from purchase than assumed."
        ),
    },
    {
        "angle": "authority — the method behind the result",
        "hook": "The process behind every campaign we run.",
        "primary_text": (
            "Same sequence every time: define the offer, test three angles, fund the one "
            "that earns it. Nothing scales until it has proven it should."
        ),
        "headline": "A method, not a guess",
        "description": "See the process",
        "hypothesis": (
            "Tests whether credibility of method drives the enquiry. A win means the "
            "audience is comparison-shopping providers rather than deciding whether to act."
        ),
        "cta": "Learn more",
    },
    {
        "angle": "direct-offer — for audiences already in market",
        "hook": "Ready now? Here is exactly what you get.",
        "primary_text": (
            "The offer, in plain terms, with what happens after you enquire. No discovery "
            "maze, no drip sequence before anyone talks to you."
        ),
        "headline": "The offer, in plain terms",
        "description": "Get started",
        "cta": "Get started",
        "hypothesis": (
            "Tests whether a share of this audience is already in market. A win means "
            "budget should shift toward high-intent placements."
        ),
    },
    {
        "angle": "objection-handling — why they have not bought yet",
        "hook": "The reason you have put this off is probably valid.",
        "primary_text": (
            "Most people delay because the last attempt cost time and produced little. "
            "Here is what is different, and what you can verify before committing."
        ),
        "headline": "Addressing the real hesitation",
        "description": "See the difference",
        "cta": "See how it works",
        "hypothesis": (
            "Tests whether the blocker is scepticism rather than awareness. A win means "
            "proof assets should lead the funnel."
        ),
    },
    {
        "angle": "contrarian — challenge a category belief",
        "hook": "More content is not the problem you have.",
        "primary_text": (
            "Volume without a hypothesis produces noise. One clear offer tested properly "
            "beats a full calendar that teaches you nothing."
        ),
        "headline": "Fewer, better bets",
        "description": "Rethink the plan",
        "cta": "Book a call",
        "hypothesis": (
            "Tests whether challenging the audience's assumption earns attention. A win "
            "means education-led creative is viable."
        ),
    },
)


def _copy_pack_payload(references: list[str]) -> dict[str, Any]:
    concepts = []
    for index, reference in enumerate(references):
        angle = _ANGLE_SET[index % len(_ANGLE_SET)]
        concepts.append(
            {
                "concept_id": reference,
                "angle": angle["angle"],
                "hook": angle["hook"],
                "primary_text": angle["primary_text"],
                "headline": angle["headline"],
                "description": angle["description"],
                "cta": angle["cta"],
                "tone": "Direct, credible",
                "audience": "The client's stated target audience",
                "objective": None,
                "hypothesis": angle["hypothesis"],
            }
        )
    return {
        "concepts": concepts,
        "data_limitations": ["No historical creative performance available for this client."],
    }


_VISUAL_SET = (
    {
        "creative_concept": "The moment of realising the numbers do not add up",
        "composition": "Off-centre subject, upper-left third clear for a headline",
        "subject": "The client's practitioner reviewing work in progress",
        "environment": "The real workplace described in the client profile",
        "lighting": "Window light from the left, soft shadow, no fill",
        "style": "Documentary photography, muted palette from the brand colours",
        "text_overlay": "Headline sits in the cleared upper-left third",
    },
    {
        "creative_concept": "The method, laid out physically",
        "composition": "Overhead flat lay, centred, even margins",
        "subject": "The tools and outputs of the client's actual service",
        "environment": "Clean neutral surface in brand tones",
        "lighting": "Diffuse overhead, minimal shadow",
        "style": "Editorial product photography, high detail",
        "text_overlay": "Short headline along the lower edge",
    },
    {
        "creative_concept": "The outcome, in use",
        "composition": "Subject on the right, negative space left",
        "subject": "A customer benefiting from the client's service",
        "environment": "The setting where the service is delivered",
        "lighting": "Warm late-afternoon daylight",
        "style": "Natural lifestyle photography, no posing",
        "text_overlay": "Offer line in the left negative space",
    },
)


def _concept_pack_payload(
    references: list[str],
    *,
    needs_video: bool,
    aspect_ratios: list[str] | None = None,
) -> dict[str, Any]:
    ratios = list(aspect_ratios) if aspect_ratios else ["1:1"]
    specs = []
    for index, reference in enumerate(references):
        visual = _VISUAL_SET[index % len(_VISUAL_SET)]
        specs.append(
            {
                "concept_id": reference,
                "creative_concept": visual["creative_concept"],
                "visual_direction": {
                    "composition": visual["composition"],
                    "subject": visual["subject"],
                    "environment": visual["environment"],
                    "lighting": visual["lighting"],
                    "style": visual["style"],
                    "brand_elements": ["Brand colour palette", "Brand typography for the overlay"],
                    "text_overlay": visual["text_overlay"],
                },
                "aspect_ratios": ratios,
                "image_prompt": (
                    f"{visual['subject']}, {visual['environment']}. {visual['composition']}. "
                    f"{visual['lighting']}. {visual['style']}. Deliberate negative space for a "
                    "short headline; no rendered text in the image."
                ),
                "video_prompt": (
                    f"Single continuous shot: {visual['subject']} in {visual['environment']}. "
                    "Slow push in over six seconds, handheld, natural light. Message carried "
                    "by the action, not by on-screen text."
                )
                if needs_video
                else None,
                "negative_constraints": [
                    "no rendered headline text",
                    "no stock-photo poses",
                    "no invented certifications",
                ],
            }
        )
    return {
        "specs": specs,
        "data_limitations": ["No prior creative assets available to match visual style against."],
    }


_PARENT_ID = re.compile(r"`parent_concept_id` on every variation to\s*([A-Za-z0-9]+)")
_VARIATION_COUNT = re.compile(r"Produce\s+(\d+)\s+variations")

#: One axis per variation, each with a stated hypothesis — the property the real
#: agent is judged on, so the mock upholds it too.
_VARIATION_SET = (
    {
        "axis": "hook",
        "hook": "Still doing this the hard way?",
        "hypothesis": (
            "Tests whether a familiarity-based opening beats a cost-of-inaction opening. "
            "A win suggests the audience recognises the problem but not its price."
        ),
    },
    {
        "axis": "cta",
        "cta": "See pricing",
        "hypothesis": (
            "Tests whether a lower-commitment ask converts better than booking a call. "
            "A win means the current CTA is asking too much too early."
        ),
    },
    {
        "axis": "audience_angle",
        "audience": "Operators earlier in the buying process, still scoping options",
        "hypothesis": (
            "Tests whether the message travels to a less decided segment. A win widens "
            "the addressable audience; a loss argues for tighter targeting."
        ),
    },
    {
        "axis": "tone",
        "tone": "Plain and understated",
        "hypothesis": (
            "Tests whether a quieter register earns more trust than a direct one for this "
            "audience."
        ),
    },
    {
        "axis": "visual",
        "hypothesis": (
            "Tests whether showing the outcome outperforms showing the problem. A win "
            "shifts creative direction toward result-led imagery."
        ),
    },
    {
        "axis": "composition",
        "hypothesis": (
            "Same subject, tighter crop. Tests whether visual hierarchy is limiting "
            "attention rather than the idea itself."
        ),
    },
)


def _variation_pack_payload(raw: str) -> dict[str, Any]:
    parent_match = _PARENT_ID.search(raw)
    parent = parent_match.group(1) if parent_match else "A"
    count_match = _VARIATION_COUNT.search(raw)
    count = int(count_match.group(1)) if count_match else 3
    needs_media = "image_prompt` (and `video_prompt`" in raw
    used = set(re.findall(r"which you must not reuse:\s*\[([^\]]*)\]", raw))
    taken = {part.strip().strip("'\"") for group in used for part in group.split(",")}
    taken.add(parent)

    alphabet = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
    available = [letter for letter in alphabet if letter not in taken]

    variations = []
    for index in range(max(1, count)):
        spec = _VARIATION_SET[index % len(_VARIATION_SET)]
        visual_axis = spec["axis"] in {"visual", "composition", "format"}
        reference = available[index] if index < len(available) else f"V{index + 1}"
        variation: dict[str, Any] = {
            "parent_concept_id": parent,
            "reference": reference,
            "axis": spec["axis"],
            "hypothesis": spec["hypothesis"],
            "creative_type": ("image" if visual_axis and needs_media else "copy"),
            "hook": spec.get("hook"),
            "primary_text": None,
            "headline": None,
            "description": None,
            "cta": spec.get("cta"),
            "tone": spec.get("tone"),
            "audience": spec.get("audience"),
            "aspect_ratio": None,
            "visual_direction": None,
            "image_prompt": None,
            "video_prompt": None,
            "negative_constraints": [],
        }
        if visual_axis and needs_media:
            variation["image_prompt"] = (
                "The client's customer using the service in its real setting, tight waist-up "
                "crop, natural side light, documentary style, negative space right for a short "
                "headline; no rendered text."
            )
            variation["negative_constraints"] = ["no rendered headline text", "no stock-photo poses"]
        variations.append(variation)

    return {
        "variations": variations,
        "data_limitations": ["No variation performance history available to prioritise axes."],
    }


def _blueprint_payload(references: list[str]) -> dict[str, Any]:
    primary = references[0] if references else "A"
    ad_sets = [
        {
            "name": "Prospecting — Core Audience",
            "audience": "The client's stated target audience, unsegmented for the first learning phase",
            "optimization": "leads",
            "placements": ["Feed", "Reels"],
            "budget_share": 0.7,
        },
        {
            "name": "Retargeting — Prior Engagers",
            "audience": "People who engaged with the client's content or visited the site",
            "optimization": "leads",
            "placements": ["Feed"],
            "budget_share": 0.3,
        },
    ]
    ads = []
    for index, reference in enumerate(references or [primary]):
        angle = _ANGLE_SET[index % len(_ANGLE_SET)]
        ads.append(
            {
                "name": f"Concept {reference} — {angle['headline']}",
                "ad_set_name": ad_sets[0]["name"] if index < 2 else ad_sets[1]["name"],
                "concept_id": reference,
                "headline": angle["headline"],
                "primary_text": angle["primary_text"],
                "cta": angle["cta"],
                "creative_type": "image",
                "destination": None,
            }
        )
    return {
        "campaign_name": "Primary Offer — Lead Capture",
        "ad_sets": ad_sets,
        "ads": ads,
        "notes": (
            "Structure is a proposal for review. No platform campaign, ad set or ad ids "
            "exist and nothing has been sent to any platform."
        ),
        "data_limitations": [
            "No historical audience performance available to justify a finer split."
        ],
    }
