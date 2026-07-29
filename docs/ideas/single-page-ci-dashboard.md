# Single-Page CI Dashboard with Auto-Discovery

## Problem Statement

How might we make the dashboard instantly show the right repo's CI status
without requiring manual navigation, manual repo entry, or manual sync?

## Recommended Direction

Merge Dashboard / Runs / Sync into one page with a repo dropdown at the top.
Sort repos so broken ones (last run failed or agent EXHAUSTED) float to the top,
with a colored status dot per repo (red/green/gray). On server startup,
auto-backfill the last 50 runs per repo. Live updates via SSE. No polling.

## Key Assumptions to Validate

- [ ] `discover_repos()` returns all repos without pagination issues
- [ ] Backfilling 50 runs × N repos on startup completes within reasonable time
- [ ] The "broken" sort (last run failed OR EXHAUSTED) catches what matters

## MVP Scope

### UI Changes (3 templates + 1 API endpoint)

- **`dashboard.html`**: Replace current static page. Add repo `<select>` at top
  populated from `GET /api/repos` (returns repo list with latest status + colored dots).
  Selecting a repo triggers `hx-get="/api/dashboard/partial?repo=..."` to filter
  the table + metrics. Dropdown options have `data-status` attribute for dot styling.
- **`sync.html`**: Keep file but remove from nav. Accessible via direct URL only.
- **`dashboard_partial`** in `ui/app.py`: Accept optional `repo` query param.
  When set, filter `get_all_runs()` + metrics to that repo only.

### Backend Changes (3 files)

- **`ui/app.py`**: Add `GET /api/repos` endpoint — calls `discover_repos(org)`,
  then for each repo queries `get_run_status()` for latest status. Returns sorted
  list (broken first) with `{name, status, dot_color}` for each repo.
- **`server.py`** lifespan: After starting org sync, do one immediate backfill pass
  (50 runs per repo, concurrent with semaphore=5 to avoid hammering Forgejo).
- **`continuous_sync.py`**: `_org_sync_loop` already handles periodic discovery.
  No changes needed — backfill on startup is the missing piece.

### Live Updates (already works)

- Webhooks → SSE broadcasts → dashboard updates instantly
- New repos discovered by `discover_repos()` get continuous sync started automatically
- Backfilled runs show up in the table immediately via the partial refresh

## Not Doing (and Why)

- Separate Runs/Sync pages — they're now the same page with a filter
- Polling-based refreshes — SSE is sufficient for live updates
- Pagination or virtual scrolling — 50 runs × repos fits comfortably
- Per-repo agent config — all repos use the same behavior
- Search/autocomplete for dropdown — 50 repos don't need it
- Trend charts or pass-rate stats — focused on current state, not history
- Role-based repo access — all authenticated users see all repos

## Implementation Plan (5 Slices)

| Slice | File(s) | Change |
|-------|---------|--------|
| 1 | `ui/app.py`, `services/run_tracker.py` | Add `GET /api/repos` endpoint — discover repos, get latest status, return sorted with dots |
| 2 | `ui/app.py` | Update `dashboard_partial` to accept `?repo=` filter param |
| 3 | `ui/templates/dashboard.html` | Rewrite with repo dropdown + status dots + filtered view |
| 4 | `server.py` | Add startup backfill in lifespan (50 runs/repo, concurrent semaphore=5) |
| 5 | `ui/templates/base.html`, `sync.html` | Hide sync page from nav, keep accessible via URL |

## Open Questions

- None resolved — assumptions will be validated during implementation
