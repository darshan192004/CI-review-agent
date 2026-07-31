# Externalized Session Loop for the CI Review Agent

## Problem Statement
How might we let the agent autonomously heal failing Forgejo CI — retrying its own failed fixes up to a hard limit — without risking an infinite push loop, on a system that currently only half-works, shipping today?

## Recommended Direction
**Externalize the retry loop.** The webhook handler owns all control flow (should I invoke? how many attempts? is this resolved?); LangGraph becomes a single-pass pipeline (clone → fetch logs → LLM fix → push → END). A new SQLite `sessions` table, keyed by **bot-pushed head_sha**, tracks `attempt_count` (max 3), status, and the previous attempt's analysis. The infinite-loop guarantee is an explicit matcher invariant, not an accident of bookkeeping: *a bot-authored terminal webhook is consumed iff it matches an active session with attempts remaining; otherwise it is logged, alerted, and dropped.*

Sequenced as: **diagnose-first** (one live human-failure E2E against the current code) → **fix the discovered breakages** (clone auth / commit-author resolution / webhook parsing) → **externalize** → **verify all three paths live** (human-failure→green, bot-failure→retry→green, exhaustion→EXHAUSTED on dashboard).

## Key Assumptions to Validate
- [ ] Forgejo's terminal webhook for a bot-triggered run carries a stable `head_sha` usable for matching — test with one real bot push before building the matcher
- [ ] Commit-author/email resolution via the Forgejo API reliably identifies bot commits — test the exact path in `webhook_handler.py:65-93`
- [ ] One session per branch at a time is acceptable — if not, `head_sha` keying (already chosen) still holds

## MVP Scope
- `sessions` table (keyed `head_sha`) + matcher in `webhook_handler.py`
- Bot-commit skip → session routing; remove the in-graph bot guard (`nodes.py:473-495`) and the `poll_ci_status`/`route_ci_outcome` loop (`graph.py:66-77`)
- Persist prior analysis in the session row; feed as `previous_context`
- Wire `git_manager.py` commit identity to `settings.ci_bot_username`/`ci_bot_email`
- Live verification: failure→green, failure→retry→green, exhaustion→EXHAUSTED

## Not Doing (and Why)
- **Durable queue (Redis/ARQ)** — right for multi-org; pure overhead for a self-hosted single user today
- **PostgreSQL migration** — SQLite + a lock is fine at this scale; the spec doc can keep Postgres for later
- **Multi-repo/org management** — `repo_discovery.py` exists; leave it out of today's loop
- **PR/branch-isolation mode** — push to the original branch (already enforced by `git_manager.py:176`); no PR flow today
- **UI work beyond EXHAUSTED status** — the SSE layer already streams status; don't build new UI

## Open Questions
- ~~Session key: head_sha vs (repo, branch)?~~ → **Resolved: bot-pushed head_sha**
- ~~Sequencing: diagnose-first or externalize immediately?~~ → **Resolved: diagnose-first**
- Does Forgejo actually send you a `run_attempt`-distinct webhook per bot push, or do you need the session row to be the sole attempt authority?
