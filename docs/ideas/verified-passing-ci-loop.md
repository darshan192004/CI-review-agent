# Verified Passing CI Loop (Live Forgejo)

## Problem Statement

How might we get the near-complete CI Review Agent to a *verified, legitimate passing
CI state in live Forgejo today* — proving the session-retry loop (human failure → fix →
bot failure → retry ≤3 → green or EXHAUSTED) end-to-end, with no mocks or simulated
webhooks?

## Recommended Direction

**Fix the 3 loop-breaking bugs, lock them with regression tests, then run the full live
loop.** The graph never terminates after a push (`graph.py` re-loops `FIX_PUSHED` into
`llm_fix_code` with stale logs), which prevents `last_fix_sha` from ever being recorded —
so the bot's own follow-up webhook is dropped as `skipped_bot`, and retry/escalation/close
are all unreachable. Two more bugs compound it: bot terminal webhooks in GitHub-compatible
`workflow_run` format (`status="completed"` + `conclusion`) are ignored by the bot branch,
and bot retries re-derive a fresh session by fix-sha that can never match the
head-sha-keyed original, silently resetting `attempt_count=1` each retry → unbounded loop,
no escalation, lost `previous_context`. Standardize on the current `head_sha` +
`last_fix_sha` keying and make it actually work.

## Key Assumptions to Validate

- [ ] Forgejo is reachable at `localhost:3000` and the runner is registered (user confirmed)
- [ ] Forgejo's bot-run webhook carries a stable `head_sha`/commit identity that survives push → next webhook
- [ ] `ci_bot_username` isn't set in `.env` (only defaults) — bot detection currently rides entirely on `ci_bot_email` matching
- [ ] A valid Forgejo PAT and LLM API key are available to configure `.env`

## MVP Scope

**Part A — Bug fixes (code):**
1. `graph.py`: route `FIX_PUSHED` (and `PASSED`) → `END`, so the graph terminates after push and `webhook_handler` records `last_fix_sha` while the session is still `active`
2. `webhook_handler.py`: bot terminal branch must also treat `conclusion`/`action` (`status=="completed"` + conclusion) as success/failure, not just `status` strings
3. `webhook_handler.py`: reuse the session found via `get_session_by_fix_sha` as the retry session instead of re-deriving via `get_session_by_head_sha` — preserves `attempt_count` + `previous_analysis`, enables escalation
4. Regression tests for: FIX_PUSHED routing, bot failure via `completed`+`conclusion=failure`, two consecutive retries, escalation at 3rd failure
5. Secondary fixes: Forgejo `list_runs` plain-array parsing (`ci_client.py`), Forgejo log-fetch API path (`ci_client.py`), `is_duplicate` coverage of `FIX_PUSHED`, remove `run_tracker.clear()` on shutdown (`server.py`) so restarts don't reset history

**Part B — Live verification (gated):** bring up Forgejo+runner → org webhook → human-failure→green, bot-failure→retry→green, exhaustion→EXHAUSTED on dashboard

## Not Doing (and Why)

- **Redis/ARQ queue, PostgreSQL** — pure overhead for a single self-hosted user shipping today
- **PR/branch-isolation mode** — push to original branch is already enforced by `git_manager.py`
- **New dashboard UI** — SSE already streams status; only EXHAUSTED badge needs to show up correctly
- **Multi-org/tenant config** — out of today's loop
- **GitHub live testing** — Forgejo-first, apply learnings later

## Open Questions

- Where does the live Forgejo + runner run? (Confirmed: `localhost:3000`, user `forgejo_admin`)
- Forgejo PAT + LLM API key needed for live verification — to be provided by the user
