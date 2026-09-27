# SEO Continuous Monitoring (M9.15)

## Overview

M9.15 adds scheduled and manual SEO monitoring that **detects, compares, records, and alerts** on persisted SEO data from M9.1–M9.14.

Monitoring does **not**:

- approve SEO actions
- execute SEO actions
- publish content
- modify client websites autonomously

## Architecture

```
Worker.run_once()
  └─ ensure_seo_monitor_tick()          [when SEO_MONITOR_SCHEDULER_ENABLED]
       └─ seo.monitor_scheduler_tick
            ├─ discover_monitor_targets()
            ├─ enqueue seo.monitor_cycle per org (bounded)
            └─ schedule_next_monitor_tick()

seo.monitor_cycle handler
  └─ SeoMonitoringService.run_monitor_cycle()
       ├─ optional: enqueue seo.crawl (M9.1)
       ├─ optional: SearchConsoleIntelligenceService.run_sync() (M9.3)
       ├─ optional: competitor crawls (M9.6)
       └─ evaluate pending actions / alerts

seo.crawl completion (when monitoring_run_id present)
  └─ SeoMonitoringService.process_crawl_completion()
       └─ compare findings, update snapshots, create/resolve alerts
```

Reuses the existing PostgreSQL `BackgroundJob` queue and worker — **no parallel scheduler**.

## Configuration

Per-organization `seo_monitoring_config` (default: `monitoring_enabled=false`):

| Field | Purpose |
|-------|---------|
| `site_root_url` | Verified crawl root (SSRF-checked) |
| `crawl_monitoring_enabled` | Schedule site crawls |
| `search_console_monitoring_enabled` | Schedule GSC syncs |
| `competitor_monitoring_enabled` | Schedule competitor crawls |
| `*_interval_hours` | Per-channel frequency |
| `alert_cooldown_hours` | Dedupe window for repeat alerts |
| Threshold fields | Deterministic GSC / finding alert thresholds |

Global settings (`config.py`):

- `SEO_MONITOR_SCHEDULER_ENABLED` (default: false)
- `SEO_MONITOR_INTERVAL_MINUTES` (default: 1440)
- `SEO_MONITOR_MAX_ORGS_PER_CYCLE` (default: 50)

## API

| Method | Route | RBAC |
|--------|-------|------|
| GET | `/api/v1/seo/monitoring` | Viewer+ |
| PATCH | `/api/v1/seo/monitoring` | Admin (`integration_connect`) |
| POST | `/api/v1/seo/monitoring/run` | Member+ + rate limit |
| GET | `/api/v1/seo/monitoring/runs` | Viewer+ |
| GET | `/api/v1/seo/monitoring/runs/{id}` | Viewer+ |
| GET | `/api/v1/seo/monitoring/alerts` | Viewer+ |
| GET | `/api/v1/seo/monitoring/alerts/{id}` | Viewer+ |
| POST | `/api/v1/seo/monitoring/alerts/{id}/acknowledge` | Member+ |

## Alert types

- `technical_new_high_finding`
- `technical_high_count_increase`
- `gsc_clicks_decline` / `gsc_impressions_decline` / `gsc_position_change`
- `gsc_new_opportunity`
- `action_pending_approval`
- `authorization_required`
- `monitoring_job_failed`

## Dedupe & lifecycle

- Alerts use stable `dedupe_key` per org (unique constraint)
- Open/acknowledged alerts are not duplicated within cooldown
- Resolved conditions auto-resolve matching alerts
- Lifecycle: `open` → `acknowledged` → `resolved`

## Dashboard integration

`GET /api/v1/seo/dashboard` includes a `monitoring` panel with enabled state, open alert count, last run timestamps, and scheduler status. Backward compatible — existing panels unchanged.

## Security

- Tenant isolation on all queries
- SSRF validation on configured site root
- Alert text sanitized (no HTML)
- No OAuth tokens in logs, errors, or alert payloads
- Manual run rate-limited (`seo_monitor_manual`: 2/hour/org default)

## Limitations

- No email/WhatsApp delivery (M9.16)
- No weekly report generation (M9.16)
- GSC sync remains synchronous inside monitor cycle handler
- Competitor monitoring limited to configured active competitors
- Global scheduler must be enabled for automatic ticks

## M9.16 handoff

M9.16 weekly reporting can consume:

- `seo_monitoring_runs` history (status, alerts_generated, changes_detected)
- `seo_monitoring_alerts` (open/resolved summaries by type/severity)
- `seo_monitoring_snapshots` (metric baselines and deltas)
- Dashboard `monitoring` panel timestamps

M9.16 was **not** implemented in M9.15.
