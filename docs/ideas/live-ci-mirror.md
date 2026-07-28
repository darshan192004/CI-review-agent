# Live CI Mirror

## Problem Statement

**How Might We** make the CI Review Agent dashboard a true real-time mirror of Forgejo/GitHub Actions — automatically syncing all CI state changes (including manual reruns) without requiring manual intervention?

## Recommended Direction

**Webhook-First with Adaptive Polling Fallback**

Forgejo fires webhooks for all CI events — pushes, failures, successes, AND manual reruns (validated via testing). This means webhooks can be our primary real-time mechanism. A lightweight adaptive poller serves as a safety net for dropped webhooks and provides initial state sync.

**Why this direction:**
- Webhooks are already working and proven reliable for reruns
- Near real-time updates (<5s latency) without constant API polling
- Adaptive polling conserves API calls while catching missed events
- Leverages existing infrastructure (SSE, `run_tracker`, `broadcast_event`)

**Key architectural changes:**
1. **Adaptive poller:** 5s for active runs (`RUNNING`/`AGENT_WORKING`), 60s for completed, skip if webhook arrived recently
2. **Webhook health tracking:** `last_webhook_at` timestamp per repo to detect silent failures
3. **SSE reconnection refresh:** Full state sync from DB when dashboard reconnects
4. **Configurable auto-fix on rerun:** Setting to enable/disable agent auto-fix when rerun webhooks arrive
5. **Continuous sync mode:** After initial config + first sync, auto-sync stays active for the session

## Key Assumptions to Validate

- [ ] Forgejo doesn't aggressively rate-limit (test with continuous polling for 10+ minutes)
- [ ] SSE reconnection works reliably across browser tab focus/blur cycles
- [ ] Team workflow matches "dashboard stays open" pattern (validate with team feedback)

## MVP Scope

**In:**
- Adaptive poller (5s active / 60s idle)
- Webhook health tracking per repo
- SSE reconnection full-state refresh
- Configurable auto-fix on rerun (`auto_fix_reruns` setting)
- Dashboard auto-updates without page refresh (already working via SSE, just needs reliability fixes)

**Out:**
- Multi-repo concurrent sync (start with single repo, scale later)
- Webhook retry/delivery guarantees (Forgejo handles this)
- Persistent background sync when all tabs closed (session-based for now)
- GitHub-specific rerun testing (focus on Forgejo first, GitHub later)

## Not Doing (and Why)

- **Not building a full event queue/streaming system** — SSE + SQLite is sufficient for a 2-5 person team
- **Not implementing webhook signature rotation** — Forgejo handles this, we just verify
- **Not adding WebSocket support** — SSE is simpler, already works, and covers our use case
- **Not building a mobile-responsive dashboard** — Desktop-first for a dev team tool
- **Not implementing offline/pwa mode** — Overkill for an internal CI monitoring tool
- **Not adding GitHub Actions rerun testing** — Validate Forgejo first, apply learnings to GitHub later

## Open Questions

- What poll interval should be the default for active runs? (5s feels aggressive — maybe 10s?)
- Should the auto-fix on rerun be per-repo or global setting?
- Do we need a "sync status" indicator on the dashboard showing last webhook received / last poll?
