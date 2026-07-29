# Task Checklist: Fix Dashboard

## Task 1: Add org config to settings UI
- [ ] Add `"forgejo_org"` and `"github_org"` to `allowed_keys` in `ui/app.py`
- [ ] Verify org fields appear in `/config` page
- [ ] Verify saving persists to `.env`

## Task 2: Surface repo discovery errors
- [ ] Modify `discover_repos()` to not silently catch all exceptions
- [ ] Update `/api/repos` endpoint to return error info
- [ ] Update dashboard JS to show error state when discovery fails

## Task 3: Add SSE metric events
- [ ] Add periodic metrics snapshot (every 1s) to SSE event generator
- [ ] Emit `event: metrics_update` with JSON payload `{active, success, failed, uptime}`
- [ ] Verify via browser SSE inspector

## Task 4: SSE-only dashboard
- [ ] Remove `hx-trigger="every 10s"` from dashboard.html
- [ ] Add JS listener for SSE `metrics_update` events → update metric cards
- [ ] Add initial state fetch on page load (single `/api/dashboard/partial` call)
- [ ] Verify zero flicker at 1s updates

## Task 5: Startup env validation
- [ ] Add startup checks for empty org, default base_url, missing token
- [ ] Log clear warnings with fix instructions
