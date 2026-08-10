# Media Generation Implementation Report

**Date:** 2026-08-09  
**Break point found:** Providers were only `none` / `demo` concept stubs — no real API adapters, no object-storage upload of bytes, and jobs could be marked complete without a file. Campaign builder stored prompts only.

---

## Image provider

**Configured / Not Configured (depends on env)**

| Value | Behavior |
|-------|----------|
| `IMAGE_PROVIDER=none` | **NOT CONFIGURED** — honest error, no fake COMPLETED |
| `IMAGE_PROVIDER=demo` + `DEMO_MODE=true` | **Working (DEMO)** — generates a real PNG file labeled DEMO, stores it |
| `IMAGE_PROVIDER=openai` + `IMAGE_API_KEY` or `OPENAI_API_KEY` | **Working (live)** — OpenAI Images API → bytes → storage |

Verified in tests with `IMAGE_PROVIDER=demo`: COMPLETED only after PNG bytes stored; media endpoint returns valid image bytes.

---

## Video provider

**Configured / Not Configured (depends on env)**

| Value | Behavior |
|-------|----------|
| `VIDEO_PROVIDER=none` | **NOT CONFIGURED** |
| `VIDEO_PROVIDER=demo` | Does **not** invent an MP4 — fails honestly (`DEMO_VIDEO_FILE_NOT_GENERATED`) |
| `VIDEO_PROVIDER=replicate` + `VIDEO_API_KEY` + `VIDEO_MODEL` | **Implemented** — async job, poll, download valid video, store |

Without Replicate credentials: **Not Configured / Not Working** for playable video (by design).

---

## Image generation

**Working** for demo PNG path (tested).  
**Working** for OpenAI when keys present (adapter implemented; requires credentials for live call).

Pipeline:

`QUEUED → GENERATING → UPLOADING → COMPLETED` (only if file exists in storage)

---

## Video generation

**Adapter Working / Live Depends on Credentials**

Pipeline:

`QUEUED → SUBMITTED → PROCESSING → DOWNLOADING → UPLOADING → COMPLETED`

Never COMPLETED without valid video bytes (`ftyp` MP4 check).

---

## Object storage

**Working** — `LocalObjectStorage` (`STORAGE_BACKEND=local`, `STORAGE_LOCAL_PATH`).

Keys:

`organizations/{org}/clients/{client}/campaigns/{campaign|none}/images|videos/...`

Authenticated serve: `GET /api/v1/creative/media/{asset_id}`

---

## Background jobs

**Working (inline + poll)** — image jobs processed in-request through status transitions; video jobs submit to provider and poll (bounded), with `GET /creative/videos/jobs/{id}` continuing poll. DB `image_jobs` / `video_jobs` persist status. Full Redis/Celery worker still optional for long videos.

---

## Frontend

**Working** — Creative Library:

- Provider status badges
- Generate image / video
- Authenticated `MediaPreview` (blob URL)
- DEMO badge when applicable
- Variation action

---

## Tests

**Passed:** `15` API tests including `tests/test_media_generation.py`  
**Frontend:** `tsc --noEmit` clean

---

## Exact remaining blocker

1. **Live OpenAI images** require `IMAGE_PROVIDER=openai` and `IMAGE_API_KEY` / `OPENAI_API_KEY`.
2. **Live videos** require `VIDEO_PROVIDER=replicate`, `VIDEO_API_KEY`, and `VIDEO_MODEL` (owner/name or version).
3. Demo video **intentionally** does not fabricate MP4 files.

---

## Env setup

```bash
# Demo real PNG files (local)
DEMO_MODE=true
IMAGE_PROVIDER=demo
VIDEO_PROVIDER=none
STORAGE_BACKEND=local
STORAGE_LOCAL_PATH=./storage

# Live images
DEMO_MODE=false
IMAGE_PROVIDER=openai
IMAGE_API_KEY=sk-...
IMAGE_MODEL=dall-e-3

# Live videos
VIDEO_PROVIDER=replicate
VIDEO_API_KEY=r8_...
VIDEO_MODEL=owner/model-name
```

## API

| Method | Path |
|--------|------|
| GET | `/api/v1/creative/providers` |
| POST | `/api/v1/creative/images/generate` |
| GET | `/api/v1/creative/images/jobs/{id}` |
| POST | `/api/v1/creative/videos/generate` |
| GET | `/api/v1/creative/videos/jobs/{id}` |
| GET | `/api/v1/creative/media/{asset_id}` |
| POST | `/api/v1/creative/{asset_id}/variations` |
| GET | `/api/v1/creative/assets` |

Autopilot `/autopilot/image/generate` and `/video/generate` now use the same media pipeline.
