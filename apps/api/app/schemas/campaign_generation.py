"""
Typed contracts for the P2-A campaign engine.

Two groups of models live here:

**Agent output schemas** — validated straight from the provider response by
`BaseAgent.run`. They are the reason a malformed or hallucinated shape fails
loudly instead of half-populating a campaign.

**API request/response schemas** — what a caller sends and reads.

Every agent output carries `data_limitations`. That field is the mechanism behind
the no-fabrication rule: an agent that lacks the data to support a claim is
required to say so there rather than produce a confident number. Anything
numeric that would be a performance claim (CTR, CPL, ROAS, revenue) is absent
from these schemas by design — a field that does not exist cannot be invented.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

#: Axes a variation is allowed to change. Constrained so "variation" cannot
#: degrade into an unlabelled reword.
VariationAxis = Literal[
    "hook",
    "visual",
    "offer",
    "cta",
    "tone",
    "composition",
    "format",
    "audience_angle",
]

CreativeType = Literal["image", "video", "copy"]


class Evidence(BaseModel):
    """
    A claim tied to where it came from.

    `source` names a field in the client context or the available-metrics dict.
    A recommendation that cites no source is a judgement, not a finding, and the
    UI presents the two differently.
    """

    claim: str
    source: str
    value: str | None = None


# ---------------------------------------------------------------------------
# CampaignStrategyAgent
# ---------------------------------------------------------------------------


class CampaignStrategy(BaseModel):
    """The 13-section strategy document a reviewer reads before approving."""

    current_situation: str
    problem: str
    opportunity: str
    target_audience: str
    positioning: str
    core_message: str
    offer_strategy: str
    creative_strategy: str
    channel_strategy: str
    campaign_objective: str
    success_metrics: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    data_limitations: list[str] = Field(default_factory=list)
    #: Preserved so a claim that rests on analytics can be traced back to the
    #: metric it rests on.
    evidence: list[Evidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# CreativeBriefAgent
# ---------------------------------------------------------------------------


class CampaignBriefDraft(BaseModel):
    """
    The brief as the agent proposes it.

    Budget is absent on purpose: it comes from the request, never from the model.
    A model-chosen budget would be an invented spend commitment.
    """

    campaign_name: str
    offer: str | None = None
    audience: str | None = None
    pain_points: list[str] = Field(default_factory=list)
    value_proposition: str | None = None
    messaging_angle: str | None = None
    tone: str | None = None
    brand_constraints: list[str] = Field(default_factory=list)
    success_metrics: list[str] = Field(default_factory=list)
    creative_direction: str | None = None
    cta: str | None = None
    data_limitations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# CopyAgent
# ---------------------------------------------------------------------------


class CopyConcept(BaseModel):
    """One distinct marketing hypothesis expressed as ad copy."""

    concept_id: str
    angle: str
    hook: str
    primary_text: str
    headline: str
    description: str | None = None
    cta: str
    tone: str | None = None
    audience: str | None = None
    objective: str | None = None
    #: Why this is a different bet from the others, not a reword of them.
    hypothesis: str | None = None


class CopyConceptPack(BaseModel):
    concepts: list[CopyConcept] = Field(default_factory=list)
    data_limitations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# CreativeConceptAgent
# ---------------------------------------------------------------------------


class VisualDirection(BaseModel):
    composition: str
    subject: str
    environment: str
    lighting: str
    style: str
    brand_elements: list[str] = Field(default_factory=list)
    text_overlay: str | None = None


class CreativeConceptSpec(BaseModel):
    """
    The visual half of a concept, including the prompts that will be sent to the
    image and video providers.

    Prompts are grounded in the client, product, audience, offer and brand voice.
    A generic prompt is a defect: it produces stock-looking output that tells a
    reviewer nothing about whether the angle works.
    """

    concept_id: str
    creative_concept: str
    visual_direction: VisualDirection
    aspect_ratios: list[str] = Field(default_factory=list)
    image_prompt: str
    video_prompt: str | None = None
    negative_constraints: list[str] = Field(default_factory=list)


class CreativeConceptPack(BaseModel):
    specs: list[CreativeConceptSpec] = Field(default_factory=list)
    data_limitations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# VariationAgent
# ---------------------------------------------------------------------------


class VariationSpec(BaseModel):
    parent_concept_id: str
    reference: str
    axis: VariationAxis
    hypothesis: str
    creative_type: CreativeType = "copy"
    hook: str | None = None
    primary_text: str | None = None
    headline: str | None = None
    description: str | None = None
    cta: str | None = None
    tone: str | None = None
    audience: str | None = None
    aspect_ratio: str | None = None
    visual_direction: VisualDirection | None = None
    image_prompt: str | None = None
    video_prompt: str | None = None
    negative_constraints: list[str] = Field(default_factory=list)


class VariationPack(BaseModel):
    variations: list[VariationSpec] = Field(default_factory=list)
    data_limitations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# CampaignBuilderAgent
# ---------------------------------------------------------------------------


class AdSetBlueprint(BaseModel):
    name: str
    audience: str
    optimization: str
    placements: list[str] = Field(default_factory=list)
    #: Share of the daily budget, 0–1. A share rather than an amount so the
    #: server owns the arithmetic and the model cannot inflate the total.
    budget_share: float = Field(default=1.0, ge=0.0, le=1.0)


class AdBlueprint(BaseModel):
    name: str
    ad_set_name: str
    concept_id: str
    headline: str
    primary_text: str
    cta: str
    creative_type: CreativeType = "image"
    destination: str | None = None


class CampaignBlueprint(BaseModel):
    campaign_name: str
    ad_sets: list[AdSetBlueprint] = Field(default_factory=list)
    ads: list[AdBlueprint] = Field(default_factory=list)
    notes: str | None = None
    data_limitations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# API — requests
# ---------------------------------------------------------------------------


class CampaignGenerateRequest(BaseModel):
    """
    A "Create Campaign with AI" request.

    Quantities are validated here for an early, precise error, and clamped again
    server-side against `GenerationLimits` — the clamp is the control, this is
    the courtesy.
    """

    client_id: UUID
    platform: str = "meta"
    objective: str = "lead_generation"
    campaign_name: str | None = Field(default=None, max_length=200)
    total_budget: Decimal | None = Field(default=None, ge=0)
    daily_budget: Decimal | None = Field(default=None, ge=0)
    monthly_budget: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", max_length=8)
    duration_days: int = Field(default=30, ge=1, le=365)
    offer: str | None = Field(default=None, max_length=2000)
    audience: str | None = Field(default=None, max_length=2000)
    tone: str | None = Field(default=None, max_length=120)
    cta: str | None = Field(default=None, max_length=120)
    concept_quantity: int = Field(default=3, ge=1, le=20)
    image_quantity: int = Field(default=0, ge=0, le=50)
    video_quantity: int = Field(default=0, ge=0, le=20)
    variation_quantity: int = Field(default=0, ge=0, le=50)
    aspect_ratios: list[str] = Field(default_factory=list)
    #: Repeating a request with the same key returns the original run instead of
    #: starting a second one, so a double-click cannot double-spend.
    idempotency_key: str | None = Field(default=None, max_length=200)

    @field_validator("aspect_ratios")
    @classmethod
    def _cap_ratios(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))[:4]


class VariationGenerateRequest(BaseModel):
    count: int = Field(default=3, ge=1, le=12)
    #: Restrict which axes may change. Empty means the agent chooses, which is
    #: usually what a user wants from a single button.
    axes: list[VariationAxis] = Field(default_factory=list)
    #: Produce real media for each variation as well as copy.
    generate_media: bool = False


class ConceptRegenerateRequest(BaseModel):
    """Re-run media generation for one concept without re-running the campaign."""

    image_quantity: int = Field(default=1, ge=0, le=8)
    video_quantity: int = Field(default=0, ge=0, le=4)
    aspect_ratio: str | None = None


class ApprovalDecision(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


class RejectionDecision(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


# ---------------------------------------------------------------------------
# API — responses
# ---------------------------------------------------------------------------


class GenerationStageOut(BaseModel):
    """
    One stage of a run, exactly as the worker recorded it.

    `completed`/`total` are real counts of finished work, which is what lets the
    UI say "Images 2/3" without estimating anything.
    """

    key: str
    label: str
    status: str
    detail: str | None = None
    completed: int = 0
    total: int = 0


class CampaignGenerationRunOut(BaseModel):
    id: UUID
    organization_id: UUID
    client_id: UUID
    brief_id: UUID | None = None
    campaign_id: UUID | None = None
    status: str
    platform: str
    objective: str
    stages: list[GenerationStageOut] = Field(default_factory=list)
    result: dict = Field(default_factory=dict)
    data_limitations: list[str] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    background_job_id: UUID | None = None
    concept_quantity: int = 0
    image_quantity: int = 0
    video_quantity: int = 0
    variation_quantity: int = 0
    demo_mode: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    #: True once the run will not change again without a new request.
    terminal: bool = False
    poll_url: str | None = None

    model_config = {"from_attributes": True}


class CampaignBriefOut(BaseModel):
    id: UUID
    client_id: UUID
    campaign_name: str
    platform: str
    objective: str
    offer: str | None = None
    audience: str | None = None
    pain_points: list = Field(default_factory=list)
    value_proposition: str | None = None
    messaging_angle: str | None = None
    tone: str | None = None
    brand_constraints: list = Field(default_factory=list)
    total_budget: Decimal | None = None
    daily_budget: Decimal | None = None
    monthly_budget: Decimal | None = None
    currency: str = "USD"
    success_metrics: list = Field(default_factory=list)
    creative_direction: str | None = None
    cta: str | None = None
    data_limitations: list = Field(default_factory=list)
    strategy: dict = Field(default_factory=dict)
    data_source: str = "live"
    created_at: datetime

    model_config = {"from_attributes": True}


class ConceptAssetOut(BaseModel):
    """
    A generated file, or an honest account of why there is not one.

    `url` is only ever populated for an asset whose bytes are in this tenant's
    storage prefix; a concept whose provider was unconfigured reports
    NOT_CONFIGURED and no url at all.
    """

    id: UUID | None = None
    job_id: UUID | None = None
    kind: Literal["image", "video"]
    status: str
    url: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: int | None = None
    aspect_ratio: str | None = None
    provider: str | None = None
    error: str | None = None
    error_code: str | None = None
    retryable: bool = False
    demo: bool = False


class CreativeVariationOut(BaseModel):
    id: UUID
    parent_concept_id: UUID
    reference: str
    axis: str
    hypothesis: str
    creative_type: str
    hook: str | None = None
    primary_text: str | None = None
    headline: str | None = None
    description: str | None = None
    cta: str | None = None
    tone: str | None = None
    audience: str | None = None
    aspect_ratio: str | None = None
    visual_direction: dict = Field(default_factory=dict)
    image_prompt: str | None = None
    video_prompt: str | None = None
    negative_constraints: list = Field(default_factory=list)
    status: str
    archived_at: datetime | None = None
    data_source: str = "live"
    created_at: datetime
    assets: list[ConceptAssetOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class CreativeConceptOut(BaseModel):
    id: UUID
    client_id: UUID
    campaign_id: UUID | None = None
    brief_id: UUID | None = None
    reference: str
    angle: str
    hook: str | None = None
    primary_text: str | None = None
    headline: str | None = None
    description: str | None = None
    cta: str | None = None
    tone: str | None = None
    audience: str | None = None
    objective: str | None = None
    platform: str | None = None
    visual_direction: dict = Field(default_factory=dict)
    image_prompt: str | None = None
    video_prompt: str | None = None
    negative_constraints: list = Field(default_factory=list)
    aspect_ratios: list = Field(default_factory=list)
    status: str
    archived_at: datetime | None = None
    data_limitations: list = Field(default_factory=list)
    data_source: str = "live"
    created_at: datetime
    assets: list[ConceptAssetOut] = Field(default_factory=list)
    variations: list[CreativeVariationOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class AdSetOut(BaseModel):
    id: UUID
    name: str
    audience: str | None = None
    daily_budget: Decimal | None = None
    optimization: str | None = None
    placements: list = Field(default_factory=list)
    status: str

    model_config = {"from_attributes": True}


class AdOut(BaseModel):
    id: UUID
    ad_set_id: UUID
    name: str
    concept_id: UUID | None = None
    variation_id: UUID | None = None
    creative_asset_id: UUID | None = None
    headline: str | None = None
    primary_text: str | None = None
    cta: str | None = None
    destination: str | None = None
    status: str

    model_config = {"from_attributes": True}


class CampaignApprovalOut(BaseModel):
    review_status: str
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    approval_comment: str | None = None
    rejected_by: UUID | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    #: Populated only by a real integration confirming an external campaign.
    #: Always None in P2-A, and the UI must not claim publication without it.
    external_id: str | None = None
    can_approve: bool = False


class CampaignSummaryOut(BaseModel):
    id: UUID
    client_id: UUID
    name: str
    platform: str
    objective: str | None = None
    review_status: str
    status: str
    audience: str | None = None
    total_budget: Decimal | None = None
    daily_budget: Decimal | None = None
    monthly_budget: Decimal | None = None
    currency: str = "USD"
    generated_by_ai: bool = False
    data_source: str = "live"
    created_at: datetime

    model_config = {"from_attributes": True}


class MediaProviderStatusOut(BaseModel):
    image_provider: str
    image_configured: bool
    video_provider: str
    video_configured: bool
    storage_backend: str
    demo_mode: bool
    message: str


class PlatformAvailabilityOut(BaseModel):
    key: str
    label: str
    aspect_ratios: list[str]
    default_image_ratio: str
    default_video_ratio: str
    placements: list[str]
    supports_video: bool
    headline_max_chars: int
    primary_text_max_chars: int
    #: Connection state from this organization's integrations. `false` means
    #: exactly that — never "assume connected".
    connected: bool = False
    connection_status: str = "not_connected"
    #: False throughout P2-A. Publishing is out of scope.
    publishing_supported: bool = False
    notes: str = ""


class ObjectiveOptionOut(BaseModel):
    key: str
    label: str
    description: str
    optimization: str
    success_metrics: list[str]


class AspectRatioOptionOut(BaseModel):
    key: str
    label: str
    width: int
    height: int
    usage: str
    orientation: str


class GenerationLimitsOut(BaseModel):
    max_concepts: int
    max_images: int
    max_videos: int
    max_variations: int


class CampaignGeneratorOptionsOut(BaseModel):
    """Everything the generator form needs, resolved server-side."""

    platforms: list[PlatformAvailabilityOut]
    objectives: list[ObjectiveOptionOut]
    aspect_ratios: list[AspectRatioOptionOut]
    limits: GenerationLimitsOut
    media: MediaProviderStatusOut


class CampaignPackageOut(BaseModel):
    """The complete reviewable output of a generation run."""

    campaign: CampaignSummaryOut | None = None
    brief: CampaignBriefOut | None = None
    strategy: CampaignStrategy | None = None
    concepts: list[CreativeConceptOut] = Field(default_factory=list)
    ad_sets: list[AdSetOut] = Field(default_factory=list)
    ads: list[AdOut] = Field(default_factory=list)
    approval: CampaignApprovalOut | None = None
    run: CampaignGenerationRunOut | None = None
    data_limitations: list[str] = Field(default_factory=list)
    media: MediaProviderStatusOut | None = None
    #: Stated plainly on every package so no reader can mistake this for a live
    #: campaign.
    publishing_note: str = (
        "Publishing is not implemented. This package is a proposal for human "
        "review; no advertising money has been spent and nothing was sent to "
        "any ad platform."
    )
