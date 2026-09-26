"""Heuristic thresholds for technical SEO analysis — not universal Google limits."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnalysisThresholds:
    title_min_chars: int = 10
    title_max_chars: int = 70
    meta_min_chars: int = 50
    meta_max_chars: int = 160
    alt_max_chars: int = 125
    recommended_max_depth: int = 4
    redirect_chain_warning: int = 2
    redirect_chain_high: int = 4
    near_duplicate_title_ratio: float = 0.9
    near_duplicate_meta_ratio: float = 0.9
    oversized_response_bytes: int = 1_048_576


DEFAULT_THRESHOLDS = AnalysisThresholds()
