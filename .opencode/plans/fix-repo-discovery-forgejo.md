# Plan: Fix Repo Discovery for Forgejo Users

## Problem

`ForgejoCIClient.list_repos()` at `services/ci_client.py:253` uses `/api/v1/orgs/{org}/repos` which **only works for Forgejo organizations**. If `FORGEJO_ORG` is set to a username (like `testadmin`), the endpoint returns a 404 with `"user redirect does not exist"`.

Actual Forgejo API behavior verified against `localhost:3000`:

| Call | Result |
|------|--------|
| `GET /api/v1/orgs/testadmin/repos` | 404 — user is not an org |
| `GET /api/v1/users/testadmin/repos` | 200 — returns 2 repos |
| `GET /api/v1/orgs` | 200 — empty `[]` (no orgs exist) |

## Recommended Fix

Modify `ForgejoCIClient.list_repos()` to try `/api/v1/orgs/{org}/repos` first, and on 404, fall back to `/api/v1/users/{org}/repos`.

This way:
- **Existing org users**: no change — first call succeeds
- **User-only setups** (like `testadmin`): first call returns 404, fallback succeeds
- **Both fail**: exception propagates as before (caught by `/api/repos` handler)

## Task

### Task: Fall back to user repos endpoint on 404

**Description:** In `ForgejoCIClient.list_repos()`, catch `httpx.HTTPStatusError` when status is 404 and retry with `/api/v1/users/{org}/repos`. Keep the `@retry` decorator for transient failures.

**Files touched:**
- `services/ci_client.py` — modify `ForgejoCIClient.list_repos()` only

**Acceptance criteria:**
- [ ] `list_repos("testadmin")` returns `["testadmin/test-commit-comments", "testadmin/test-failing-ci"]` against `localhost:3000`
- [ ] `list_repos("ci-test-org")` against a real org still works (no regression)
- [ ] If both endpoints fail, the exception propagates (not silently eaten)

**Verification:**
- [ ] `pytest tests/ -x -q` — all existing tests pass
- [ ] Manual: after fix + setting `FORGEJO_ORG=testadmin` in `.env`, `/api/repos` returns the 2 test repos
- [ ] Manual: dashboard dropdown shows the repos

**Estimated scope:** Small (1 file, ~10 lines changed)

### After the fix: set FORGEJO_ORG in .env

Simply add to `.env`:
```
FORGEJO_ORG=testadmin
```

No other code changes needed. Repo discovery, org sync, and backfill will all work against the user's repos.

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Forgejo version differences in API behavior | Low — 404 on non-org is consistent across Forgejo/Gitea versions | Tested against actual `localhost:3000` instance |
| User has no repos | Low — returns `[]`, dashboard shows "No repos found" | Already handled by existing JS error display |
