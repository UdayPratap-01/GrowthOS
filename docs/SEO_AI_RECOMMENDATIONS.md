# M9.7 — AI SEO Recommendations

## Overview

M9.7 converts deterministic SEO evidence from M9.1–M9.6 into **tenant-scoped, grounded AI recommendations**. The AI is an **interpreter of evidence**, not a source of SEO facts.

Pipeline:

```
M9.2 technical findings
+ M9.3 Search Console intelligence
+ M9.4 keyword opportunities
+ M9.5 topic clusters
+ M9.6 competitor/content gaps
→ bounded evidence snapshot
→ AI interpretation (structured output)
→ evidence grounding validation
→ persisted recommendations
```

## Versions

| Constant | Value |
|----------|-------|
| Algorithm | `seo_recommendation_v1` |
| Prompt | `seo_recommendation_prompt_v1` |

## Evidence sources

| Source | M9 module | Evidence ID field |
|--------|-----------|-------------------|
| `seo_finding` | M9.2 | SeoFinding.id |
| `search_console_opportunity` | M9.3 | SearchConsoleOpportunity.id |
| `keyword_opportunity` | M9.4 | KeywordOpportunity.id |
| `topic_cluster` | M9.5 | TopicCluster.id |
| `content_gap` | M9.6 | ContentGap.id |
| `competitor_page` | M9.6 | SeoCompetitorPage.id |

## Data integrity

The AI **must not invent**:

- Rankings, search volume, traffic, backlinks
- Competitor rankings, traffic, search volume
- Unsupported Search Console metrics
- Crawl observations, keyword metrics, or SEO findings not in the snapshot

Unavailable competitor fields are explicitly marked:

- `competitor_rankings`: unavailable
- `competitor_search_volume`: unavailable
- `competitor_traffic`: unavailable
- `competitor_backlinks`: unavailable

GrowthOS does **not** have authoritative competitor performance data unless a real external provider is integrated later.

## Evidence prefilter

`build_evidence_snapshot()` deterministically selects bounded evidence:

- Highest-severity technical findings (by severity, then recency)
- Top keyword opportunities by impressions
- Top topic clusters by impressions
- Top content gaps by similarity
- Recent Search Console opportunities
- Recent competitor crawl pages

Hard limits (configurable via `app.core.config`):

- `seo_recommendation_max_findings` (default 20)
- `seo_recommendation_max_keywords` (default 30)
- `seo_recommendation_max_topics` (default 20)
- `seo_recommendation_max_gaps` (default 20)
- `seo_recommendation_max_competitor_pages` (default 15)
- `seo_recommendation_max_per_run` (default 15)
- `seo_recommendation_max_prompt_chars` (default 120,000)

## Evidence snapshot

Each generation run stores an `evidence_snapshot` on `SeoRecommendationRun` containing only the bounded records sent to the AI. Provenance is retained via `evidence_refs` on each recommendation.

## Recommendation types

- `technical_seo`
- `content_refresh`
- `keyword_targeting`
- `topic_expansion`
- `content_gap`
- `metadata_optimization`
- `page_structure`
- `search_intent`
- `competitor_gap`

## Structured AI output

Output schema: `SeoRecommendationsGenerated` (Pydantic). Every recommendation requires:

- type, title, summary, rationale
- priority, impact, effort, confidence (0–1)
- evidence_refs (min 1, max 10)
- recommended_action, expected_outcome
- optional affected_urls, affected_keywords, affected_topics

Malformed output fails safely; hallucinated references are rejected.

## Grounding validation

Post-generation, `validate_and_filter_recommendations()` checks:

- Evidence source type is valid
- Evidence ID exists in the snapshot index
- affected_urls / affected_keywords / affected_topics exist in snapshot sets

Invalid recommendations are **rejected individually** — not silently repaired.

## Prompt injection defense

Competitor content and all evidence are wrapped in an untrusted boundary:

```
UNTRUSTED SEO EVIDENCE
<evidence>...</evidence>
END UNTRUSTED SEO EVIDENCE
```

System instructions explicitly forbid instruction override and metric invention.

## Provider behavior

Uses existing `BaseAgent` → `get_ai_provider()` architecture.

| Condition | Response |
|-----------|----------|
| Missing AI config | `503 ai_provider_unavailable` |
| Generation error | `502 ai_generation_failed` |
| All output fails grounding | `422 grounding_validation_failed` |
| No evidence | `400 insufficient_evidence` |
| Snapshot too large | `400 evidence_snapshot_too_large` |

No silent fallback from real AI to mock in production.

## Idempotency

`generation_key = sha256(org_id | sync_id | evidence_hash | prompt_version | algorithm_version)`

Regenerating with the same key deletes prior run + recommendations before creating new ones.

## API

Base path: `/api/v1/seo/recommendations`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/generate` | Generate recommendations |
| GET | `/` | List with filters (type, priority, status) |
| GET | `/summary` | Aggregate counts |
| GET | `/{id}` | Single recommendation |

Rate limits: `seo_recommendation_generate_limit`, `ai_limit`, AI quota.

## Frontend

Route: `/seo/recommendations`

Displays recommendations with clear separation between **deterministic evidence refs** and **AI interpretation**. Includes generate, refresh, filters, and detail panel.

## Security

- All queries organization-scoped
- Evidence ownership validated before and after AI generation
- No secrets in prompts or logs
- Generation rate limited
- RBAC via existing auth/permissions

## M9.8 handoff

M9.8 can consume `SeoRecommendation` records containing:

- id, recommendation_type, title, summary, rationale
- evidence_refs, affected_urls, affected_keywords, affected_topics
- competitor_context, recommended_action
- priority, impact, effort, confidence, limitations
- status, provider metadata, timestamps

## Known limitations

- Competitor rankings, traffic, search volume, and backlinks are unavailable
- Recommendations require at least one M9 evidence source
- AI output quality depends on evidence breadth and provider availability
- Synchronous generation (no separate job queue for M9.7)
