# Production-Ready UI & Universal Messaging MCP

## Problem Statement

How might we take a working CI self-healing backend and turn its browser surface into
something stakeholders trust and love — instant, consistent, never ugly — while giving
admins complete in-browser control (no `.env` editing), auto-discovering repos the way
WiFi connects to a network in range, and letting the agent report through team chat
channels via a new messaging MCP?

## Recommended Direction

Seven staged slices, bug-first:

1. **Fix `/runs` double-window + unify status badges** — `/runs` returns an HTML
   fragment when `HX-Request: true`; one shared badge macro replaces four divergent
   renderers. Pulsing dot only for `AGENT_WORKING`/`processing`; stable green/red/amber
   for terminal states.
2. **Repo discovery: WiFi auto-connect** — decouple discovery from the org gate.
   Per-platform `DISCOVERY_MODE` ∈ `{auto, org, user}` (default `auto` = token owner's
   own repos). Rescan on page load, SSE reconnect, config save, and TTL-cached poll.
   First-run popup asks the admin to configure explicitly; "No" falls back to `auto`.
3. **Error presentation** — structured JSON errors → existing `showToast`; empty states
   with config CTA; no raw server text in the DOM.
4. **Config dashboard expansion** — Repository Discovery panel, LLM rate-limit controls,
   and the 8 settings already in `allowed_keys` but missing from the form. Host/port
   changes documented as requiring restart.
5. **UX audit** — a11y focus/contrast, responsive runs table, loading states.
6. **Standardized commit messages** — Conventional Commits format with LLM-generated
   scope + imperative summary, enforced ≤72-char subject, assembled by a dedicated
   formatter with deterministic fallbacks.
7. **Messaging MCP scaffold (Python, in-repo)** — stdio MCP server, modular channel
   adapters (Slack/Discord/Telegram + Mattermost parity), smoke-tested via pytest.
   ADR-003 amended from Go to Python.

## Key Assumptions to Validate

- [ ] Single-process uvicorn makes live `.env` mutation via `write_env` safe (breaks
      under multi-worker — acceptable, noted)
- [ ] Native SSE/EventSource remains the real-time mechanism; no re-architecture
- [ ] Discovery token has access to the discovered repos (GitHub `users/{username}/repos`
      is public-only for other users; `auto` mode is the safest default)
- [ ] Messaging MCP ships scaffold-only; not wired into `graph.py` notification path yet
- [ ] `.env` persistence is sufficient; no DB migration

## MVP Scope

### Slice 1 — `/runs` partial + badge unification
- `ui/app.py`: `HX-Request` check on `/runs` returns table fragment only.
- New shared Jinja macro `ui/templates/_status_badge.html` used by `dashboard.html`,
  `runs.html`, and both Python badge builders (`_status_badge`, `_sse_status_badge`).

### Slice 2 — Discovery auto-connect
- `config.py`: `forgejo_discovery_mode`, `github_discovery_mode`, `forgejo_username`,
  `github_username`, `discovery_configured` (first-run flag).
- `services/ci_client.py`: mode dispatch for `list_repos` (org/user/auto).
- `services/repo_discovery.py`: resolve scope; structured result.
- `ui/app.py` `/api/repos`: returns `{status, repos, detail, configured}`.
- `dashboard.html`: status line in dropdown + first-run popup + rescan on SSE reconnect.
- `services/env_writer.py`: add new sensitive/non-sensitive keys as needed.

### Slice 3 — Error presentation
- Fragile endpoints return `{"ok": false, "detail": ...}`.
- `dashboard.html`: empty-state with config CTA replaces hardcoded repo error text.
- `app.js`: standardize toast extraction.

### Slice 4 — Config dashboard
- `config.html`: Repository Discovery panel, LLM rate limits, gated fields.
- `/api/settings`: add new keys to `allowed_keys` + int coercion.

### Slice 5 — UX audit
- A11y, responsive table, loading skeletons.

### Slice 6 — Commit messages
- `state.py`: `commit_scope`, `commit_summary`, per-file `reason`.
- `nodes.py`: Commit Header Rule in system prompt; parser fallbacks; use formatter.
- `services/commit_message.py` + `tests/test_commit_message.py`.

### Slice 7 — Messaging MCP
- `mcp_servers/messaging/`: `server.py`, `channels/{slack,discord,telegram,mattermost}.py`,
  `formats.py`; pytest smoke test.

## Not Doing (and Why)

- UI framework rewrite — HTMX + Tailwind stays
- SSE → WebSocket/polling swap — SSE already real-time
- Go messaging binary — ADR-003 amended to Python
- DB persistence for settings — `.env` + live state suffices single-process
- Tier-2 provider parity in config UI — form bloat, low value
- Background org-sync loop — trigger-based rescan delivers the WiFi behavior
- Teams/PagerDuty adapters in MCP scaffold — keep to requested + Mattermost parity

## Open Questions

- (Resolved) MCP smoke test: included. Restart hint: documented. Discovery default:
  `auto` + first-run popup.

## Sequencing

Slices 1→2 (critical) → 3→4→5 (UI/config) → 6 (commit format) → 7 (scaffold) → verify.
