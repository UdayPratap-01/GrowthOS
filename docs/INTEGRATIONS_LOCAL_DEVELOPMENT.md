# Integrations — Local Development Guide

This document describes what works on `localhost` before a production domain is configured.

## Status Summary

| Integration | Local OAuth | Read-only ops | Mutations | Public HTTPS required |
|-------------|-------------|---------------|-----------|------------------------|
| Google Ads (M7) | Yes (127.0.0.1 callback) | Discovery, status, metrics | **Disabled** | OAuth redirect only |
| YouTube | Yes | Channel, videos, analytics cache | **Disabled** | OAuth redirect only |
| WhatsApp | Yes (Meta OAuth) | WABA + phone metadata | **Disabled** | Webhooks + outbound messaging |
| Google Search Console | Yes | Sites, search analytics | **Disabled** | OAuth redirect only |
| SEO Audit Engine | Yes | HTTP crawl observations | N/A | No |
| Meta Ads (M6) | Yes* | Verify, discovery | **Disabled** | OAuth redirect* |

\*Meta OAuth redirect URI must match a URL registered in Meta Developer Console. A stable public HTTPS URL is recommended for production; local `127.0.0.1:8000` works when registered.

## Safety Defaults (unchanged)

- `CANARY_ENABLED=false`
- `AUTONOMOUS_EXECUTION_ENABLED=false`
- `OPTIMIZATION_ENABLED=false`
- No live campaign creation, budget changes, YouTube publishing, or WhatsApp messaging

## Environment Variable Names

Shared Google OAuth (Analytics, Ads, YouTube, Search Console):

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_ADS_DEVELOPER_TOKEN` (optional for Google Ads since Sept 2026)
- `GOOGLE_ADS_REDIRECT_URI` (optional override)
- `YOUTUBE_REDIRECT_URI` (optional override)
- `GOOGLE_SEARCH_CONSOLE_REDIRECT_URI` (optional override)

Meta family (Meta Ads, Instagram, WhatsApp):

- `META_APP_ID`
- `META_APP_SECRET`
- `META_REDIRECT_URI` (optional override)
- `META_WEBHOOK_VERIFY_TOKEN` (webhook verification)

Do **not** commit real values. See `.env.example` for the full list.

## What Works Locally

### Google Ads (M7 — existing, not rewritten)

- OAuth connect/callback
- Customer discovery
- Campaign discovery and read-only verification
- Pause/resume adapters exist but live mutations remain gated OFF

### YouTube

- OAuth via shared Google client
- Sync stores `channel_stats` and `recent_videos` in integration config
- Read API: `GET /api/v1/seo/integrations/youtube/videos`
- Capabilities: `GET /api/v1/seo/integrations/youtube/capabilities`

### WhatsApp

- Meta OAuth (same app as Meta Ads)
- Sync discovers WABA accounts and phone numbers (read-only Graph API)
- Read API: `GET /api/v1/seo/integrations/whatsapp/status`
- Webhook endpoints exist at `/api/v1/webhooks/whatsapp` but **production activation is PENDING_PUBLIC_HTTPS**
- Outbound messaging is **UNSUPPORTED**

### Google Search Console

- OAuth with `webmasters.readonly` scope
- Site list + search analytics sync (7-day window)
- Read API: `GET /api/v1/seo/integrations/google_search_console/analytics`
- URL Inspection API: **not wired** in this release

### SEO Audit Engine

- `POST /api/v1/seo/audit` — technical crawl observations only
- Does **not** claim index status without Search Console data
- Recommendations are advisory

## What Requires Public HTTPS

- WhatsApp webhook delivery from Meta (inbound messages)
- Stable Meta/Google OAuth redirects in production (recommended)
- Cloudflare named tunnel / production domain (not configured in this repo)

## What Requires Production Credentials

- Live Google Ads API (developer token approval)
- Live Meta Marketing API campaign visibility (asset permissions)
- Real Search Console verified properties
- Real YouTube channel with Data API quota

## API Endpoints (read-only layer)

```
POST /api/v1/seo/audit
GET  /api/v1/seo/integrations/{provider}/capabilities
GET  /api/v1/seo/integrations/youtube/videos
GET  /api/v1/seo/integrations/whatsapp/status
GET  /api/v1/seo/integrations/google_search_console/analytics
GET  /api/v1/webhooks/whatsapp   (verification — needs verify token)
POST /api/v1/webhooks/whatsapp   (signature-valid ack only; no sends)
```

Standard integration routes remain at `/api/v1/integrations/{provider}/...`.

## Production Blockers

1. No production domain purchased/configured
2. Cloudflare named tunnel not configured
3. Meta campaign discovery may return empty (external permissions)
4. WhatsApp webhooks cannot receive events without public HTTPS

## Labels

- **READY FOR LOCAL DEVELOPMENT** — OAuth, sync, read APIs, tests with mocks
- **REQUIRES PUBLIC HTTPS** — WhatsApp webhook delivery, stable OAuth in prod
- **REQUIRES PRODUCTION CREDENTIALS** — Live provider tokens with approved access
