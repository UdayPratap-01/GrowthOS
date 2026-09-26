"""Search Console intelligence (M9.3)."""

from app.seo.search_console.client import SearchConsoleClient
from app.seo.search_console.dates import DateRange, resolve_custom_range, resolve_preset_range
from app.seo.search_console.intelligence import OpportunityDraft, detect_opportunities

__all__ = [
    "SearchConsoleClient",
    "DateRange",
    "resolve_custom_range",
    "resolve_preset_range",
    "OpportunityDraft",
    "detect_opportunities",
]
