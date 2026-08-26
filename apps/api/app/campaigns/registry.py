"""
Campaign surface configuration: platforms, objectives, creative formats, limits.

Everything a new platform needs to exist lives in one `PlatformSpec` here. The
agents receive the resolved spec as data and never branch on a platform name, so
adding TikTok is a dict entry rather than an edit to six prompt builders.

Two things are deliberately *not* in this file:

- **Whether a platform is connected.** That is per-organization state, resolved
  from the `integrations` table at request time. A spec says a platform is
  *supported by the generator*, never that this customer can publish to it.
- **Whether a platform can be published to.** `publishing_supported` is False on
  every spec because P2-A does not publish. It is a field rather than an omission
  so the day publishing lands, the honest value has an obvious home.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import get_settings


@dataclass(frozen=True)
class AspectRatioSpec:
    key: str
    label: str
    width: int
    height: int
    #: What this shape is normally used for. Passed to the concept agent so a
    #: 9:16 prompt is composed for vertical, not cropped from a square idea.
    usage: str

    @property
    def orientation(self) -> str:
        if self.width == self.height:
            return "square"
        return "landscape" if self.width > self.height else "portrait"


ASPECT_RATIOS: dict[str, AspectRatioSpec] = {
    "1:1": AspectRatioSpec("1:1", "Square", 1080, 1080, "Feed posts and carousels"),
    "4:5": AspectRatioSpec("4:5", "Portrait", 1080, 1350, "Feed — maximum mobile height"),
    "9:16": AspectRatioSpec("9:16", "Vertical", 1080, 1920, "Stories, Reels, Shorts"),
    "16:9": AspectRatioSpec("16:9", "Landscape", 1920, 1080, "In-stream video and display"),
}

DEFAULT_ASPECT_RATIO = "1:1"


@dataclass(frozen=True)
class PlatformSpec:
    key: str
    label: str
    #: Which integration provider, if any, would eventually publish here. Used
    #: only to look up connection status for display — never to enable publishing.
    integration_provider: str | None
    aspect_ratios: tuple[str, ...]
    default_image_ratio: str
    default_video_ratio: str
    placements: tuple[str, ...]
    supports_video: bool = True
    #: Character budgets the copy agent is told to respect. Advisory guidance in
    #: the prompt, not a hard truncation: silently cutting a headline would ship
    #: a broken ad rather than surface a too-long one to the reviewer.
    headline_max_chars: int = 40
    primary_text_max_chars: int = 125
    description_max_chars: int = 30
    publishing_supported: bool = False
    notes: str = ""


PLATFORMS: dict[str, PlatformSpec] = {
    "meta": PlatformSpec(
        key="meta",
        label="Meta (Facebook)",
        integration_provider="meta",
        aspect_ratios=("1:1", "4:5", "9:16", "16:9"),
        default_image_ratio="1:1",
        default_video_ratio="9:16",
        placements=("Feed", "Reels", "Stories", "Marketplace", "Audience Network"),
        headline_max_chars=40,
        primary_text_max_chars=125,
        description_max_chars=30,
        notes="Structure follows campaign → ad set → ad.",
    ),
    "instagram": PlatformSpec(
        key="instagram",
        label="Instagram",
        integration_provider="instagram",
        aspect_ratios=("1:1", "4:5", "9:16"),
        default_image_ratio="4:5",
        default_video_ratio="9:16",
        placements=("Feed", "Reels", "Stories", "Explore"),
        headline_max_chars=40,
        primary_text_max_chars=125,
        description_max_chars=30,
        notes="Visual-first; hook has to land in the first frame.",
    ),
    "google": PlatformSpec(
        key="google",
        label="Google Ads",
        integration_provider="google_ads",
        aspect_ratios=("1:1", "4:5", "16:9"),
        default_image_ratio="1:1",
        default_video_ratio="16:9",
        placements=("Search", "Display", "Discovery", "YouTube"),
        headline_max_chars=30,
        primary_text_max_chars=90,
        description_max_chars=90,
        notes="Responsive assets: several short headlines rather than one long one.",
    ),
    "linkedin": PlatformSpec(
        key="linkedin",
        label="LinkedIn",
        integration_provider=None,
        aspect_ratios=("1:1", "4:5", "16:9"),
        default_image_ratio="1:1",
        default_video_ratio="16:9",
        placements=("Feed", "Sponsored Messaging"),
        headline_max_chars=70,
        primary_text_max_chars=150,
        description_max_chars=70,
        notes="Professional register; no integration exists yet, so generation only.",
    ),
}

DEFAULT_PLATFORM = "meta"


@dataclass(frozen=True)
class ObjectiveSpec:
    key: str
    label: str
    description: str
    #: What the ad set optimizes for. Passed through to the blueprint so the
    #: builder agent does not have to invent platform vocabulary.
    optimization: str
    #: Metrics a reviewer should judge this campaign by. Names only — no target
    #: values, because a target would be a fabricated benchmark.
    success_metrics: tuple[str, ...]
    requires_destination: bool = True


OBJECTIVES: dict[str, ObjectiveSpec] = {
    "lead_generation": ObjectiveSpec(
        key="lead_generation",
        label="Lead Generation",
        description="Collect qualified contact details from people with intent.",
        optimization="leads",
        success_metrics=("Cost per lead", "Lead volume", "Lead quality rate"),
    ),
    "sales": ObjectiveSpec(
        key="sales",
        label="Sales",
        description="Drive completed purchases.",
        optimization="purchases",
        success_metrics=("Cost per purchase", "Return on ad spend", "Average order value"),
    ),
    "traffic": ObjectiveSpec(
        key="traffic",
        label="Traffic",
        description="Send qualified visitors to a destination.",
        optimization="link_clicks",
        success_metrics=("Cost per click", "Click-through rate", "Landing page view rate"),
    ),
    "engagement": ObjectiveSpec(
        key="engagement",
        label="Engagement",
        description="Earn interaction that builds an addressable audience.",
        optimization="engagement",
        success_metrics=("Cost per engagement", "Engagement rate", "Saves and shares"),
        requires_destination=False,
    ),
    "awareness": ObjectiveSpec(
        key="awareness",
        label="Awareness",
        description="Reach a defined audience with a memorable message.",
        optimization="reach",
        success_metrics=("Cost per thousand impressions", "Reach", "Frequency"),
        requires_destination=False,
    ),
    "conversions": ObjectiveSpec(
        key="conversions",
        label="Conversions",
        description="Optimize for a specific tracked conversion event.",
        optimization="conversions",
        success_metrics=("Cost per conversion", "Conversion rate", "Conversion volume"),
    ),
}

DEFAULT_OBJECTIVE = "lead_generation"


@dataclass(frozen=True)
class GenerationLimits:
    """
    Server-side ceilings on one generation request.

    These are the guardrail against runaway spend: every quantity in a request is
    clamped here before any provider is called. Frontend inputs are a
    convenience; this is the control.
    """

    max_concepts: int
    max_images: int
    max_videos: int
    max_variations: int
    max_images_per_concept: int = 4
    max_videos_per_concept: int = 2

    def clamp_concepts(self, value: int | None) -> int:
        return _clamp(value, 1, self.max_concepts, default=3)

    def clamp_images(self, value: int | None) -> int:
        return _clamp(value, 0, self.max_images, default=0)

    def clamp_videos(self, value: int | None) -> int:
        return _clamp(value, 0, self.max_videos, default=0)

    def clamp_variations(self, value: int | None) -> int:
        return _clamp(value, 0, self.max_variations, default=0)


def _clamp(value: int | None, low: int, high: int, *, default: int) -> int:
    try:
        number = int(value) if value is not None else default
    except (TypeError, ValueError):
        number = default
    return max(low, min(number, high))


def generation_limits() -> GenerationLimits:
    settings = get_settings()
    return GenerationLimits(
        max_concepts=settings.max_concepts_per_generation,
        max_images=settings.max_images_per_generation,
        max_videos=settings.max_videos_per_generation,
        max_variations=settings.max_variations_per_generation,
    )


class UnknownCampaignOption(ValueError):
    """A platform, objective or aspect ratio that is not configured."""


def platform(key: str | None) -> PlatformSpec:
    spec = PLATFORMS.get((key or DEFAULT_PLATFORM).strip().lower())
    if spec is None:
        raise UnknownCampaignOption(
            f"INVALID_CAMPAIGN_REQUEST: unsupported platform {key!r}. "
            f"Supported: {', '.join(sorted(PLATFORMS))}."
        )
    return spec


def objective(key: str | None) -> ObjectiveSpec:
    spec = OBJECTIVES.get((key or DEFAULT_OBJECTIVE).strip().lower())
    if spec is None:
        raise UnknownCampaignOption(
            f"INVALID_CAMPAIGN_REQUEST: unsupported objective {key!r}. "
            f"Supported: {', '.join(sorted(OBJECTIVES))}."
        )
    return spec


def aspect_ratio(key: str | None) -> AspectRatioSpec:
    spec = ASPECT_RATIOS.get((key or DEFAULT_ASPECT_RATIO).strip())
    if spec is None:
        raise UnknownCampaignOption(
            f"INVALID_CAMPAIGN_REQUEST: unsupported aspect ratio {key!r}. "
            f"Supported: {', '.join(ASPECT_RATIOS)}."
        )
    return spec


def resolve_aspect_ratios(platform_key: str | None, requested: list[str] | None) -> list[str]:
    """
    Narrow requested ratios to those the platform actually supports.

    An unsupported ratio is dropped rather than rejected: the request is still
    satisfiable, and the reviewer sees which formats were produced. An empty
    result falls back to the platform default so a generation never produces
    zero formats silently.
    """
    spec = platform(platform_key)
    allowed = list(spec.aspect_ratios)
    if not requested:
        return [spec.default_image_ratio]
    kept = [r for r in dict.fromkeys(requested) if r in allowed]
    return kept or [spec.default_image_ratio]


def list_platforms() -> list[PlatformSpec]:
    return list(PLATFORMS.values())


def list_objectives() -> list[ObjectiveSpec]:
    return list(OBJECTIVES.values())


def list_aspect_ratios() -> list[AspectRatioSpec]:
    return list(ASPECT_RATIOS.values())


@dataclass
class PlatformAvailability:
    """Per-organization truth about a platform, for display only."""

    platform: PlatformSpec
    connection_status: str = "not_connected"
    connected: bool = False
    publishable: bool = False
    detail: str = ""
    supported_aspect_ratios: list[str] = field(default_factory=list)
