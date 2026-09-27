# SEO Weekly Reports (M9.16)

## Purpose

M9.16 turns persisted SEO intelligence (M9.1–M9.15) into structured weekly reports for planning and review.

Reports are **informational only**. They do not execute SEO actions, publish content, or modify websites.

## Architecture

```
Worker.run_once()
  └─ ensure_seo_report_tick()     [when SEO_WEEKLY_REPORT_SCHEDULER_ENABLED]
       └─ seo.report_scheduler_tick
            ├─ discover_report_targets()
            ├─ enqueue seo.report.generate per org
            └─ schedule_next_report_tick()

seo.report.generate handler
  └─ SeoWeeklyReportService.generate_report()
       ├─ SeoDashboardService.get_dashboard()
       ├─ M9.15 monitoring runs/alerts in period
       ├─ monitoring snapshots for trends
       └─ persist structured report_payload JSON
```

Reuses existing `BackgroundJob` queue — no parallel scheduler or queue.

## Data sources

| Section | Source |
|---------|--------|
| Executive summary | Dashboard overview + monitoring |
| Technical SEO | M9.14 `technical` panel (M9.2 findings) |
| Search Console | M9.14 `search_console` (M9.3 sync data) |
| Keywords / Topics | M9.14 panels (M9.4/M9.5) |
| Competitors / gaps | M9.14 `competitors` (M9.6) |
| Content | M9.14 `content` (M9.8/M9.9) |
| On-page / Schema / Links | M9.14 panels (M9.10–M9.12) |
| Actions | M9.14 `actions` (M9.13, review-only) |
| Monitoring | M9.15 runs/alerts in period |
| Trends | M9.15 snapshots |

## Weekly period

- UTC Monday–Sunday (inclusive)
- No organization timezone field exists; periods documented in `data_freshness.timezone_note`
- Custom periods supported via manual generate API

## Configuration

Per-org `seo_report_config.reporting_enabled` (default: `false`)

Global:
- `SEO_WEEKLY_REPORT_SCHEDULER_ENABLED` (default: `false`)
- `SEO_WEEKLY_REPORT_INTERVAL_MINUTES` (default: 10080 = 7 days)
- `SEO_WEEKLY_REPORT_MAX_ORGS_PER_CYCLE` (default: 50)

## API

| Method | Route | RBAC |
|--------|-------|------|
| GET | `/api/v1/seo/reports/config` | Viewer+ |
| PATCH | `/api/v1/seo/reports/config` | Admin |
| GET | `/api/v1/seo/reports` | Viewer+ |
| GET | `/api/v1/seo/reports/{id}` | Viewer+ |
| POST | `/api/v1/seo/reports/generate` | Member+ + rate limit |

## Idempotency

Unique constraint: `(organization_id, period_start, period_end, report_version)`

Completed reports for the same period are reused. Generation key dedupes background jobs.

## Limitations

- No synthetic SEO score
- No email/WhatsApp delivery
- No PDF generation (structured JSON + frontend rendering)
- Search Console subject to API latency
- Competitor data limited to configured crawls
- Deterministic aggregation only (no AI narrative by default)

## M9.17 handoff

Future milestones can consume `seo_weekly_reports.report_payload` for delivery channels, PDF export, or executive email digests without re-aggregating SEO data.
