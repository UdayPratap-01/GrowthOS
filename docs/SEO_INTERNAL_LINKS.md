# SEO Internal-Link Engine (M9.12)

## Overview

M9.12 adds a deterministic-first internal-link recommendation engine for GrowthOS SEO draft content. It analyzes crawl observations, keywords, topics, generated content, briefs, and on-page findings to suggest relevant internal links with explainable scores and anchor text.

**M9.12 generates internal-link recommendations only.**

It does **NOT**:

- publish or inject links into live websites
- modify CMS content
- autonomously execute SEO actions
- implement approval workflows (M9.13)

## Architecture

```
SeoCrawlPage observations + TopicCluster + KeywordOpportunity + SeoGeneratedContent
        ↓
build_internal_link_context()
        ↓
discover_internal_links()  [internal_link_engine_v1]
        ↓
SeoInternalLinkService.generate()  [optional AI enrichment via internal_link_prompt_v1]
        ↓
SeoInternalLinkRun + SeoInternalLinkOpportunity (PostgreSQL)
        ↓
/api/v1/seo/content/{id}/internal-links*
        ↓
SEO Content UI panel (review/copy only)
```

## Data model

| Table | Purpose |
|-------|---------|
| `seo_internal_link_runs` | Idempotent analysis run keyed by `analysis_key` |
| `seo_internal_link_opportunities` | Individual source→target recommendations |

Opportunity fields include source/target URLs, crawl page refs, anchor text, opportunity type, relevance score, confidence, score breakdown, evidence refs, limitations, and status (`suggested` by default).

M9.13 owns acceptance/rejection execution; M9.12 persists `suggested` only.

## Discovery algorithm (`internal_link_engine_v1`)

1. Resolve site root from content/brief target URL or latest crawl.
2. Build page index from latest completed crawl (`SeoCrawlPage.observations`).
3. Enrich pages with keyword/topic associations from M9.4/M9.5 data.
4. Select source pages (content target URL first).
5. Generate bounded target candidates via topic overlap, keyword overlap, orphan/deep signals.
6. Score pairs with explainable components (keyword/topic/title overlap, orphan/deep value, existing-link penalties).
7. Generate deterministic anchor text from target title/headings/keywords.
8. Skip self-links, external URLs, duplicates, and pairs below relevance threshold.
9. Optional AI enrichment for rationale/alternatives (validated before persistence).

## Scoring

Scores are in `[0, 1]` with documented breakdown:

- `keyword_overlap`
- `topic_overlap`
- `title_overlap`
- `orphan_value`
- `depth_value`
- `existing_link_penalty`
- `reciprocal_penalty`

Confidence: `high` (≥0.65), `medium` (≥0.45), else `low`.

## Security

- Internal URLs validated via M9.1 normalization + same-site checks.
- Rejects `javascript:`, `data:`, `file:`, localhost, private IPs, external domains.
- Crawled page text treated as untrusted (prompt-injection defense in AI agent).
- All queries tenant-scoped by `organization_id`.

## Idempotency

`analysis_key = sha256(org|content.id|content.generation_key|brief.generation_key|algorithm_version)`

Delete-then-insert on regenerate; `(organization_id, run_id, dedupe_key)` prevents duplicates within a run.

## API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/seo/content/{id}/internal-links` | Discover/recompute opportunities |
| GET | `/api/v1/seo/content/{id}/internal-links` | Latest run + opportunities |
| GET | `/api/v1/seo/content/{id}/internal-links/opportunities` | Filtered list |
| GET | `/api/v1/seo/content/{id}/internal-links/opportunities/{opp_id}` | Single opportunity |
| GET | `/api/v1/seo/content/{id}/internal-links/summary` | Counts by type/confidence |

Rate limit: `seo_internal_link_generate_rate_limit_per_hour` (default 24).

## M9.13 handoff

M9.13 can consume:

- `SeoInternalLinkOpportunity` rows (`suggested` status)
- Evidence refs for approval audit trails
- Anchor text + alternatives for action payloads
- Score/confidence for prioritization

M9.12 does not implement approval execution, CMS writes, or autonomous publishing.

## Migration

Alembic revision: `b2c9d4e5f106` (`seo_internal_link_engine`)
