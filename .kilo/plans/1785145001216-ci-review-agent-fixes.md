# CI Review Agent — Critical Bug Fixes

## Root Cause Verification

All reported issues verified against codebase:

| Issue | Location | Confirmed |
|-------|----------|-----------|
| Forgejo webhook filtered out | `server.py:83-86` | ✅ `event_type != "workflow_run"` returns 200 for all Forgejo events |
| Test expects 202 but no header set | `tests/test_server.py:52-58` | ✅ Missing `X-Forgejo-Event` header |
| Agent polls stale run_id after push | `nodes.py:222` | ✅ Reads original `run_id` from state, never updated |
| Forgejo log fetch reads wrong field | `services/ci_client.py:148` | ✅ `data.get("jobs", [])` on run endpoint; Forgejo requires `/tasks` endpoint |
| Commit happens even on patch failure | `nodes.py:186-193` | ✅ No early return when `patch_applied` is False |
| Off-by-one attempt counting | `nodes.py:106` + `graph.py:25-26` | ✅ Starts at 1, escalates when `>= max_retry_attempts`; only N-1 attempts |
| Duplicate .env keys | `.env:30-54` | ✅ Lowercase keys shadow uppercase keys (same values currently, but fragile) |

## Implementation Tasks

### Fix 1: Accept Forgejo workflow_run + push events
**Files:** `server.py`, `tests/test_server.py`

- `server.py:83-86`: Change filter to accept both `workflow_run` and `push`
  ```python
  event_type = request.headers.get("X-Forgejo-Event", "")
  if event_type not in ("workflow_run", "push"):
      logger.info("Ignoring Forgejo event: %s", event_type)
      return Response(status_code=200, content="Ignored event type")
  ```
- `tests/test_server.py:52-58`: Add `"X-Forgejo-Event": "workflow_run"` header to Forgejo test

### Fix 2: Discover new CI run_id after push
**Files:** `services/ci_client.py`, `nodes.py`

- `services/ci_client.py`: Add `list_runs(owner, repo, branch, limit=1)` to both `GitHubCIClient` and `ForgejoCIClient`
  - GitHub: `GET /repos/{owner}/{repo}/actions/runs?branch={branch}&per_page=1`
  - Forgejo: `GET /api/v1/repos/{owner}/{repo}/actions/runs?branch={branch}&limit=1`
  - Returns list of runs; caller extracts newest `run_id`
- `nodes.py:193-200`: After `git_manager.push()`, add:
  ```python
  await asyncio.sleep(5)
  new_runs = await ci_client.list_runs(owner, repo_name, state.get("branch", "main"))
  if new_runs:
      state["run_id"] = str(new_runs[0].get("id", state.get("run_id", "")))
  ```
- `nodes.py:222`: No change needed; it already reads `run_id` from state (which is now updated)

### Fix 3: Fix Forgejo log fetching
**File:** `services/ci_client.py`

- `ForgejoCIClient.fetch_logs()`: After getting run data, query `/api/v1/repos/{owner}/{repo}/actions/runs/{run_id}/tasks` for job/step details
- For failed steps, fetch log content via `step.log_url`
- Return parsed failure summary (same pattern as GitHub client)

### Fix 4: Skip commit/push when patch fails
**File:** `nodes.py`

- `nodes.py:186-193`: If `apply_patch` returns False, return early without committing or pushing:
  ```python
  patch_applied = await git_manager.apply_patch(analysis.unified_diff)
  if not patch_applied:
      logger.warning("Patch failed to apply, skipping commit/push")
      return {
          "llm_analysis": analysis.root_cause,
          "patch_diff": "",
          "attempt_count": attempt + 1,
          "notifications_sent": ["Patch failed to apply"],
      }
  ```

### Fix 5: Fix attempt counting
**File:** `nodes.py`

- `nodes.py:106`: Change `"attempt_count": 1` to `"attempt_count": 0`
- `nodes.py:200` and `nodes.py:209`: `attempt + 1` remains correct (0→1→2→3)
- `graph.py:26`: `attempt >= settings.max_retry_attempts` now correctly allows 3 fix attempts (1, 2, 3) before escalating at 3

### Fix 6: Clean .env duplicates
**File:** `.env`

- Remove lines 30-54 (lowercase keys) since they shadow uppercase keys with identical values
- Keep only lines 6-29 (uppercase keys)

## Validation Steps

1. Run `pytest tests/test_server.py` — Forgejo test must pass with new header
2. Run `pytest` — all tests pass
3. Verify `.env` has no duplicate keys: `sort .env | uniq -d` should return empty
4. Manual smoke test: send Forgejo webhook with `X-Forgejo-Event: push` → should return 202
