# SEO Dashboard (M9.14)

## Overview

M9.14 provides a unified, tenant-scoped SEO operating dashboard at `/seo` that aggregates persisted data from M9.1–M9.13.

**M9.14 is an SEO dashboard/operational view.**

It does **NOT** implement:
- continuous or scheduled SEO monitoring (M9.15)
- weekly SEO reports (M9.16)
- autonomous execution or automatic approvals
- CMS publishing or live-site injection
- background crawls, Search Console syncs, or AI generation on dashboard load

## Architecture

```
GET /api/v1/seo/dashboard
        ↓
SeoDashboardService.get_dashboard()
        ↓
Aggregate counts/summaries from existing M9.1–M9.13 tables & services
        ↓
/seo (frontend dashboard UI)
```

No new persistence layer. No dashboard cache table.

## Data sources

| Panel | Source |
|-------|--------|
| Overview | Rollup of all panels |
| Technical SEO | Latest completed crawl + `SeoAnalysisService.summary()` |
| Search Console | `SearchConsoleIntelligenceService.summary()` |
| Keywords | `KeywordOpportunityService.summary()` (latest sync) |
| Topics | `TopicClusteringService.summary()` (latest sync) |
| Competitors / gaps | `ContentGapService.summary()` + competitor counts |
| Content | `SeoContentBrief`, `SeoGeneratedContent` counts |
| On-page | `SeoOnPageOptimizationRun`, `SeoOnPageFinding` counts |
| Schema | `SeoSchemaArtifact`, `SeoSchemaFinding` counts |
| Internal links | Org-wide `SeoInternalLinkOpportunity` counts |
| Actions | `SeoActionService.summary()` + recent pending list |
| Needs attention | High-severity findings, pending actions, high-priority signals |

## Metrics definitions

- **No synthetic SEO score** — the dashboard uses explicit persisted signals only.
- Search Console metrics are **observed GSC data** (clicks, impressions, CTR), not search volume estimates.
- Competitor intelligence reflects **crawled competitor pages only**.
- Actions show **review_only** capability when no CMS writer is configured.

## Needs-attention logic

Items appear when existing fields indicate urgency:
- HIGH/CRITICAL technical findings
- High-priority keyword opportunities
- Pending SEO actions (M9.13)
- High-confidence internal-link opportunities
- Invalid schema validation status
- High-priority Search Console opportunities
- High-priority SEO recommendations

Maximum 20 attention items returned.

## API

| Method | Path | Auth |
|--------|------|------|
| GET | `/api/v1/seo/dashboard` | `get_current_auth` |

Response: `SeoDashboardOut` with section objects each containing `available` and optional `empty_message`.

## Frontend

- `/seo` — M9.14 dashboard (overview cards, attention list, section panels)
- `/seo/crawler` — M9.1 crawler UI (moved from `/seo`)

## Security

- All queries scoped by `organization_id`
- No secrets in dashboard response
- User/crawled text rendered as plain text (no raw HTML)
- Dashboard load triggers no mutations

## M9.15 handoff

M9.15 continuous monitoring can consume:
- Dashboard section availability flags as baseline state
- Latest crawl/sync timestamps for change detection
- Attention item sources for alert routing

M9.14 did not implement monitoring, scheduling, or alerting.

## M9.16 handoff

M9.16 weekly reports can consume:
- Dashboard aggregation endpoint or section snapshots
- Overview counts for report sections
- Attention items for executive summaries

M9.14 did not implement weekly reporting or email delivery.

## Version

Dashboard aggregation version: implicit (no separate algorithm version; uses existing service summaries)
