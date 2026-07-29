# Plan: Fix Dashboard (Repo Discovery, CI Runs, UI Flicker)

## Overview

Fix three interconnected dashboard issues:

1. **Repo dropdown empty** — `discover_repos()` silently swallows errors and returns `[]`. The user gets a blank dropdown with zero feedback. Additionally, `forgejo_org`/`github_org` are missing from the settings UI's `allowed_keys`, so they can't be configured through the web UI.
2. **No CI runs visible** — Backfill runs on startup but depends on working `discover_repos()`. If discovery fails, no runs enter the DB. Webhooks may also not be arriving.
3. **UI flicker at 1s updates** — HTMX poll (`every 10s`) replaces the ENTIRE table HTML. SSE simultaneously writes individual rows. They conflict, causing visual flash. At 1s this becomes a strobe.

## Architecture Decisions

- **Big bet: SSE-only dashboard.** Remove HTMX polling entirely. Drive all updates (table rows + metrics) through the single SSE connection that's already wired (`/api/events`). SSE already has `broadcast_event()` called from every update path (poller, webhooks, sync). We extend it to emit metric snapshots periodically.
- **Surface errors, don't swallow.** `discover_repos()` at `services/continuous_sync.py:206` and `/api/repos` at `ui/app.py:367-370` catch ALL exceptions. Change them to propagate error info to the UI.
- **Fix the settings gap.** `forgejo_org` and `github_org` are missing from `allowed_keys` at `ui/app.py:421-456`. Add them so users can configure org via `/config`.
- **1s updates are free.** Since updates become fully event-driven (no polling), status changes appear instantly. For metrics, emit a state snapshot every 1s via the existing SSE stream — one JSON message, no DOM replacement.

## Task List

### Task 1: Add org config to settings UI
**Description:** `forgejo_org` and `github_org` are missing from the settings API's `allowed_keys` set. Users can't configure them through the web UI. Add them.
- **Files:** `ui/app.py`
- **Change:** Add `"forgejo_org"` and `"github_org"` to the `allowed_keys` set at line ~421
- **Verification:** `/config` page shows org fields; saving them persists to `.env`

### Task 2: Surface repo discovery errors
**Description:** `discover_repos()` at `continuous_sync.py:206` and the `/api/repos` endpoint at `ui/app.py:367-370` both swallow ALL exceptions. Change the API response to include error details. Update the dashboard JS to show the error.
- **Files:** `services/continuous_sync.py`, `ui/app.py`, `ui/static/js/app.js`
- **Change:** Return `{"repos": [], "org": "", "error": "..."}` instead of just `[]`. Update JS to show a visible error.
- **Verification:** With wrong Forgejo config, dropdown shows "Failed to discover repos: connection refused"

### Task 3: Add SSE metric events
**Description:** The SSE endpoint (`/api/events`) only emits `ci_update` events for run status changes. Add a periodic timer (every 1s) that emits `metrics_update` events with `{active, success, failed, uptime}`. The SSE event generator already has a heartbeat at 30s — repurpose it.
- **Files:** `server.py`
- **Change:** In the SSE `event_generator`, add a timer-based metrics snapshot every 1s. Query `run_tracker.count_by_status()` and push as `event: metrics_update`.
- **Verification:** SSE stream shows `metrics_update` events every 1s

### Task 4: SSE-only dashboard (remove HTMX poll)
**Description:** Remove `hx-trigger="every 10s"` from dashboard.html. Replace it with JS handlers for SSE `ci_update` and `metrics_update` events. Initial page load fetches `/api/dashboard/partial` once. After that, all updates come via SSE — no polling, no full-table-replace, no flicker.
- **Files:** `ui/templates/dashboard.html`, `ui/static/js/app.js`
- **Change:**
  - Remove `hx-get="/api/dashboard/partial" hx-trigger="every 10s"` from the table container
  - On DOM load: fetch `/api/dashboard/partial` once and inject into `#runs-table-body`
  - Listen for SSE `ci_update` events and update individual rows
  - Listen for SSE `metrics_update` events and update metric cards
- **Verification:** Dashboard loads, updates smoothly at 1s intervals with zero flicker

### Task 5: Startup env validation
**Description:** Add startup-time validation in the server lifespan that checks common config mistakes: empty `forgejo_org`, still-default `forgejo_base_url`, missing token. Log clear warnings.
- **Files:** `server.py`
- **Change:** In `lifespan()`, check config and log warnings
- **Verification:** Server startup logs show config warnings when misconfigured

## Dependency Graph
```
Task 1 (settings keys) — no deps
Task 2 (error surfacing) — no deps
Task 3 (SSE metrics) — no deps
Task 4 (SSE dashboard) — depends on Task 3
Task 5 (env validation) — no deps
```
Tasks 1, 2, 3, 5 can be done in parallel. Task 4 requires Task 3.

## Risks
- Forgejo API `list_repos()` may return different format by Forgejo version — test against actual instance
- Removing HTMX poll might affect other features — verify no other code depends on `#runs-table-body` poll
- SSE at 1s interval adds minor overhead — negligible for single-user dashboard
