"""Deterministic topic clustering thresholds (algorithm v1)."""

from __future__ import annotations

from dataclasses import dataclass


ALGORITHM_VERSION = "v1"


@dataclass(frozen=True)
class TopicClusterThresholds:
    min_jaccard_similarity: float = 0.45
    min_shared_tokens: int = 1
    min_query_tokens: int = 1
    max_queries_per_analysis: int = 2000
    max_candidates_per_query: int = 200
    singleton_allowed: bool = True


DEFAULT_TOPIC_THRESHOLDS = TopicClusterThresholds()
