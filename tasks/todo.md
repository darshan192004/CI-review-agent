# CI Dashboard: Task Checklist

## Phase 1: Foundation — Backend API

### Task 1: Add `GET /api/repos` endpoint

**Description:** Add a new API endpoint that discovers all repos in the configured org, fetches the latest run status for each, and returns a sorted list (broken first: FAILED/EXHAUSTED at top) with `{name, status, dot_color}`. Add a `get_repo_status_summary()` method to `RunTracker` that does a single GROUP BY query to avoid N+1 lookups.

**Acceptance criteria:**
- [ ] `GET /api/repos` returns JSON array sorted with broken repos (last run FAILED/EXHAUSTED) first, then PASSED, then repos with no runs
- [ ] Each entry has `{name, status, dot_color}` where dot_color is red/green/gray
- [ ] Uses `discover_repos()` to get repo list, then `get_repo_status_summary()` for latest status
- [ ] Authenticated users can access; unauthenticated get 302 to /login
- [ ] Viewers can access (not admin-only)

**Verification:**
- [ ] `pytest tests/test_ui.py -k "test_repos"` passes
- [ ] `pytest tests/test_run_tracker.py -k "test_repo_status"` passes
- [ ] Manual: `curl /api/repos` returns sorted JSON with dot colors

**Dependencies:** None

**Files likely touched:**
- `services/run_tracker.py` — add `get_repo_status_summary()`
- `ui/app.py` — add `GET /api/repos` route
- `tests/test_ui.py` — add tests for new endpoint
- `tests/test_run_tracker.py` — add tests for new method

**Estimated scope:** Medium (4 files)

---

### Task 2: Add `?repo=` filter to `dashboard_partial`

**Description:** Update the `dashboard_partial` endpoint (and the server-side dashboard + runs page) to accept an optional `repo` query parameter. When set, filter `get_all_runs()` and metrics to only runs from that repository. Keep the "no filter" default behavior unchanged.

**Acceptance criteria:**
- [ ] `GET /api/dashboard/partial?repo=owner/repo` returns only runs for that repo
- [ ] Metrics (active/success/failed counts) are scoped to the filtered repo
- [ ] No `?repo=` param returns all runs (current behavior)
- [ ] Invalid repo returns empty table (not an error)
- [ ] Authenticated users only (existing auth pattern)

**Verification:**
- [ ] `pytest tests/test_ui.py -k "test_partial"` passes
- [ ] Manual: `/api/dashboard/partial?repo=owner/repo` returns filtered rows

**Dependencies:** None (independent of Task 1)

**Files likely touched:**
- `ui/app.py` — add `repo` param to `dashboard_partial` and the main `dashboard` route
- `tests/test_ui.py` — update/add tests for filtered partial

**Estimated scope:** Small (2 files)

---

## Checkpoint: Backend API
- [ ] `GET /api/repos` returns correct sorted data
- [ ] `GET /api/dashboard/partial?repo=X` filters correctly
- [ ] All existing tests pass: `pytest`
- [ ] Ruff lint passes: `ruff check .`

---

## Phase 2: Frontend

### Task 3: Rewrite `dashboard.html` with repo dropdown

**Description:** Replace the static dashboard with a single-page view. Add a repo `<select>` dropdown at the top populated via `hx-get="/api/repos"`. Each option has a `data-status` attribute for CSS dot styling. Selecting a repo triggers `hx-get="/api/dashboard/partial?repo=..."` to replace the table + metrics. The "All Repos" option shows the unfiltered view. Keep SSE live-update working.

**Acceptance criteria:**
- [ ] Dropdown loads repos from `/api/repos` on page init via HTMX
- [ ] Each option shows a colored status dot (red/green/gray) via `data-status` CSS
- [ ] Selecting a repo from dropdown filters the metrics and table to that repo
- [ ] Selecting "All Repos" resets to unfiltered view
- [ ] SSE live updates still push into the table (global stream, repo column visible)
- [ ] "Recent Runs" heading + table structure preserved
- [ ] Empty state shown when no runs match

**Verification:**
- [ ] `pytest tests/test_ui.py` passes (existing dashboard tests)
- [ ] Manual: load page → see dropdown → select repo → table/metrics filter
- [ ] Manual: SSE events still appear in table after filtering
- [ ] Manual: "All Repos" restores unfiltered view

**Dependencies:** Task 1, Task 2

**Files likely touched:**
- `ui/templates/dashboard.html` — full rewrite with dropdown + filtered partial
- `tests/test_ui.py` — add dashboard HTML tests for dropdown presence

**Estimated scope:** Medium (2 files)

---

## Checkpoint: Dashboard UI
- [ ] Dropdown populated on page load
- [ ] Filtering works end-to-end
- [ ] SSE updates still flow
- [ ] All tests pass: `pytest`

---

## Phase 3: Startup & Navigation

### Task 4: Add startup backfill in server lifespan

**Description:** After the org sync starts in the lifespan, do one immediate backfill pass: for each discovered repo, fetch the last 50 runs concurrently (semaphore=5 to avoid hammering Forgejo). Use `discover_repos()` and `list_runs()`, reusing the status mapping from `continuous_sync.py`. This ensures the dashboard has data immediately on startup without waiting for the periodic sync.

**Acceptance criteria:**
- [ ] On server startup, after `start_org_sync()`, backfill runs for all discovered repos
- [ ] 50 runs per repo, limited by semaphore=5 concurrent requests
- [ ] Backfilled runs appear in the database (visible via dashboard)
- [ ] Does not block server from accepting requests (runs as background task)
- [ ] Handles errors gracefully (one repo failure doesn't stop others)

**Verification:**
- [ ] `pytest tests/test_server.py -k "test_backfill"` passes
- [ ] Manual: check server startup logs for backfill progress

**Dependencies:** None (can parallelize with Phase 2)

**Files likely touched:**
- `server.py` — add backfill logic in lifespan
- `services/continuous_sync.py` — extract backfill helper if needed
- `tests/test_server.py` — add backfill tests

**Estimated scope:** Medium (3 files)

---

### Task 5: Hide sync page from navigation

**Description:** Remove the Sync link from the navigation bar in `base.html`. The `/sync` page still exists and is accessible via direct URL. This prevents user confusion while maintaining backward compatibility for bookmarks.

**Acceptance criteria:**
- [ ] Sync tab removed from nav in `base.html`
- [ ] `/sync` still loads when accessed directly
- [ ] No other nav items affected

**Verification:**
- [ ] Manual: check nav bar has no Sync tab
- [ ] Manual: navigate to `/sync` directly — page loads correctly

**Dependencies:** None (trivial, can do anytime)

**Files likely touched:**
- `ui/templates/base.html` — remove Sync link from nav

**Estimated scope:** XS (1 file, 1 line removed)

---

## Checkpoint: Complete
- [ ] All 5 tasks implemented and verified
- [ ] `pytest` passes
- [ ] `ruff check .` passes
- [ ] Manual E2E: start server → dashboard loads with backfilled data → dropdown shows repos → filter works → SSE updates arrive
- [ ] Review with human before merging
