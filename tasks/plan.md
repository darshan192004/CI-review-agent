# Implementation Plan: Single-Page CI Dashboard with Auto-Discovery

## Overview

Merge Dashboard / Runs / Sync into a single page with a repo dropdown at the top. Repos with broken CI (last run failed or agent EXHAUSTED) float to the top with a colored status dot. On server startup, auto-backfill the last 50 runs per repo. Live updates via SSE — no polling.

## Architecture Decisions

- **Repo status query**: Add `get_repo_status_summary()` to `RunTracker` that returns latest run status per repo via a single GROUP BY query — no N+1 per-repo lookups.
- **Filter by repo**: `dashboard_partial` already returns HTML. Adding `?repo=` filtering is a one-line SQL change (WHERE clause) — no new endpoint needed.
- **Backfill on startup**: Use `asyncio.gather` with a semaphore of 5 in the lifespan, calling `_continuous_sync_loop`-style logic for each discovered repo. Reuse `discover_repos()` and `list_runs()`.
- **UI state**: The repo dropdown is populated on page load via `hx-get="/api/repos"`. Selecting a repo triggers `hx-get="/api/dashboard/partial?repo=..."` to filter the table + metrics.
- **Nav cleanup**: Sync link removed from `base.html` nav. The `/sync` page still works via direct URL.

## Task List

### Phase 1: Backend API (Foundation)

- [ ] Task 1: Add `GET /api/repos` endpoint — discover repos, sort broken-first, return with status dots
- [ ] Task 2: Add `?repo=` filter to `dashboard_partial` — filter runs + metrics per repo

### Checkpoint: Backend API
- [ ] `GET /api/repos` returns sorted repo list with correct dot colors
- [ ] `GET /api/dashboard/partial?repo=owner/repo` filters to that repo's runs only
- [ ] All existing tests pass
- [ ] New tests cover both endpoints

### Phase 2: Frontend Dashboard

- [ ] Task 3: Rewrite `dashboard.html` with repo dropdown, status dots, and filtered view

### Checkpoint: Dashboard UI
- [ ] Dashboard loads with repo dropdown populated from `/api/repos`
- [ ] Selecting a repo filters the table and metrics
- [ ] SSE live updates still work after filtering
- [ ] Manual test with browser

### Phase 3: Startup & Navigation

- [ ] Task 4: Add startup backfill in server lifespan (50 runs/repo, semaphore=5)
- [ ] Task 5: Hide sync page from navigation

### Checkpoint: Complete
- [ ] All acceptance criteria met
- [ ] All tests pass
- [ ] Lint and typecheck pass
- [ ] Manual smoke test: startup → dashboard shows backfilled data → dropdown filters work

## Dependency Graph

```
Task 1 (GET /api/repos)
    └── Task 3 (dashboard UI dropdown — consumes /api/repos)

Task 2 (partial filter)
    └── Task 3 (dashboard UI filter — consumes ?repo= param)

Task 4 (startup backfill) — independent of UI changes
Task 5 (nav cleanup) — independent of logic changes
```

Tasks 1 → 3 are sequential. Tasks 4 and 5 can be done in parallel with each other or after Task 1.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `discover_repos()` pagination missing repos | Med | Verify limit=100 is sufficient; add pagination loop if needed |
| Backfill 50 runs × N repos too slow on startup | Med | Semaphore=5 limits concurrency; runs are non-blocking |
| Dashboard partial repo filter breaks SSE updates | High | The SSE stream is global — filtering is client-side (dropdown filters which data is displayed); the partial endpoint filters server-side but SSE still pushes all events |
| Repo dropdown stale (new repos discovered after page load) | Low | Page refreshes handle it; auto-refresh of dropdown is out of scope |

## Open Questions

- Should the repo dropdown auto-refresh periodically? (Not in MVP scope — manual page reload is sufficient)
- Should "All Repos" show aggregate across all repos or only the latest 10 runs globally? (Latest 10 across all repos, matching current behavior)
