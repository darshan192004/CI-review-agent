# Implementation Plan: Full Security Hardening & RBAC

## Overview

Complete security overhaul of CI Review Agent: authentication with RBAC (admin/viewer), mandatory webhook verification, XSS prevention, LLM output validation, SSRF protection, and CDN hardening. The codebase currently has zero authentication, optional webhook verification, and multiple XSS/injection vectors.

## Architecture Decisions

1. **Session tokens via HTTP-only cookies** using `secrets.token_urlsafe(32)` with in-memory session store
2. **Credentials via `.env` only** - `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `VIEWER_USERNAME`, `VIEWER_PASSWORD`, `SECRET_KEY`
3. **FastAPI dependencies** for auth - `get_current_user` and `require_admin_role` injected per-route
4. **Forbidden paths guard** - block LLM from modifying `.env*`, `.github/`, `Dockerfile`, `server.py`, `services/auth.py`
5. **Branch isolation** - LLM fixes push to `autofix/ci-run-<run_id>`, never to default branch
6. **`html.escape()` everywhere** for f-string HTML construction
7. **`passlib[bcrypt]`** for password hashing

## Dependency Graph

```
Task 1: Auth Engine (services/auth.py)         [FOUNDATION]
    |
    +-- Task 2: Login/Logout UI & Routes        [depends on 1]
    |       |
    |       +-- Task 3: Protect All Routes      [depends on 2]
    |
    +-- Task 5: Mandatory Webhook Verification  [depends on 1]
    |
Task 4: LLM Safety & Branch Isolation           [independent]
Task 6: XSS Prevention                          [independent]
Task 7: SSRF & Command Injection                [independent]
Task 8: Error Sanitization                      [independent]
Task 9: CDN SRI & .env Hardening                [independent]

Task 10: Tests & Verification                   [depends on all]
```

## Task List

### Phase 1: Authentication Foundation
- [ ] Task 1: Create `services/auth.py`
- [ ] Task 2: Create login page + routes
- [ ] Task 3: Protect all routes

### Phase 2: Security Remediation
- [ ] Task 4: LLM safety & branch isolation
- [ ] Task 5: Mandatory webhook verification
- [ ] Task 6: XSS prevention
- [ ] Task 7: SSRF & command injection
- [ ] Task 8: Error sanitization
- [ ] Task 9: CDN SRI & .env hardening

### Phase 3: Tests & Verification
- [ ] Task 10: Update tests, run verification

## Detailed Task Specifications

### Task 1: Auth Engine (services/auth.py)

**Description:** Create the authentication module with password hashing, session token management, and FastAPI dependency functions.

**Acceptance criteria:**
- [ ] Exports: `hash_password()`, `verify_password()`, `create_session()`, `validate_session()`, `get_current_user`, `require_admin_role`, `User` dataclass
- [ ] Passwords hashed with `passlib[bcrypt]`, salt rounds >= 12
- [ ] Session tokens: `secrets.token_urlsafe(32)`, in-memory dict with TTL (default 24h)
- [ ] `get_current_user`: reads `session_token` cookie; returns `User` or 401 (with `HX-Redirect: /login` for HTMX)
- [ ] `require_admin_role`: raises 403 if role != "admin"
- [ ] Credentials from env: `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `VIEWER_USERNAME`, `VIEWER_PASSWORD`, `SECRET_KEY`
- [ ] No hardcoded credentials

**Files:** `services/auth.py` (new), `pyproject.toml` (add `passlib[bcrypt]`)
**Scope:** S

### Task 2: Login Page & Routes

**Description:** Create login UI and `/login` GET/POST + `/logout` POST routes.

**Acceptance criteria:**
- [ ] `ui/templates/login.html` - Tailwind-styled form, username/password fields
- [ ] `GET /login` renders page (no auth required)
- [ ] `POST /login` validates creds, sets HTTP-only Secure SameSite=Lax cookie, redirects to `/`
- [ ] `POST /logout` clears cookie, redirects to `/login`
- [ ] Failed login: generic "Invalid credentials" message
- [ ] Works with HTMX form submission

**Files:** `ui/templates/login.html` (new), `ui/app.py` (add routes)
**Scope:** S
**Depends:** Task 1

### Task 3: Protect All Routes

**Description:** Apply auth dependencies to every route.

**Acceptance criteria:**
- [ ] Read routes (`GET /`, `/config`, `/runs`, `/api/dashboard/partial`, `/api/settings`, `/api/events`) - require valid session (viewer or admin)
- [ ] Write routes (`PUT /api/settings`, `POST /api/trigger-test-run`, `POST /api/test/*`) - require admin role
- [ ] Unauthenticated browser -> 302 to `/login`
- [ ] Unauthenticated HTMX -> 401 with `HX-Redirect: /login`
- [ ] Viewer accessing admin routes -> 403
- [ ] Webhook endpoints and `/health`/`/status` remain unauthenticated

**Files:** `ui/app.py`, `server.py`, `tests/test_ui.py`, `tests/test_server.py`
**Scope:** M
**Depends:** Tasks 1, 2

### Task 4: LLM Safety & Branch Isolation

**Description:** Prevent LLM from modifying sensitive files, validate syntax, isolate branches.

**Acceptance criteria:**
- [ ] `FORBIDDEN_PATHS` list: `.github/`, `.forgejo/`, `.gitea/`, `Dockerfile`, `docker-compose.yml`, `.env*`, `services/auth.py`, `server.py`, `pyproject.toml`, `.gitignore`
- [ ] `validate_llm_patch()` checks file paths against forbidden list, raises `ValueError`
- [ ] Called in `node_llm_fix_code` before `apply_file_changes()`
- [ ] `py_compile` syntax check on `.py` files before commit
- [ ] Branch isolation: push to `autofix/ci-run-{run_id}` instead of source branch
- [ ] New `autofix_branch` parameter in `WorkspaceGitManager`

**Files:** `nodes.py`, `services/git_manager.py`, `tests/test_llm_safety.py` (new)
**Scope:** M

### Task 5: Mandatory Webhook Verification

**Description:** Make webhook secrets required and reject unsigned requests.

**Acceptance criteria:**
- [ ] Startup WARNING if `forgejo_webhook_secret` or `github_webhook_secret` empty
- [ ] Webhook without signature header + secret configured -> 401
- [ ] Webhook with invalid signature -> 401
- [ ] Webhook with no secret configured -> 200 "Ignored" (graceful skip)
- [ ] Reuses existing `webhook_verify.py` logic

**Files:** `server.py`, `tests/test_server.py`
**Scope:** S

### Task 6: XSS Prevention

**Description:** Escape all dynamic data in HTML f-strings. Fix innerHTML.

**Acceptance criteria:**
- [ ] `server.py:112-124` SSE HTML: all `meta.get()` values wrapped with `html.escape()`
- [ ] `server.py:148` `_sse_status_badge` fallback: escape `status`
- [ ] `ui/app.py:36` `_status_badge` fallback: escape `status`
- [ ] `ui/app.py:207-248` `dashboard_partial`: all run fields escaped
- [ ] `ui/static/js/app.js:94` `showToast`: use `textContent` instead of `innerHTML`

**Files:** `server.py`, `ui/app.py`, `ui/static/js/app.js`
**Scope:** S

### Task 7: SSRF & Command Injection

**Description:** Validate outbound URLs and make MCP command read-only via API.

**Acceptance criteria:**
- [ ] `services/net_utils.py` with `validate_safe_url(url)`: reject loopback, RFC1918, `169.254.169.254`
- [ ] Applied to `test_forgejo` and `test_ollama` endpoints
- [ ] `mcp_server_command` removed from `allowed_keys` in `update_settings` (env-only)
- [ ] `mcp_server_command` excluded from `_get_settings()` response

**Files:** `services/net_utils.py` (new), `ui/app.py`
**Scope:** S

### Task 8: Error Sanitization

**Description:** Global exception handler, sanitize error responses.

**Acceptance criteria:**
- [ ] Global `exception_handler` in `server.py` catches all unhandled exceptions
- [ ] Returns `{"detail": "An internal server error occurred."}` to clients
- [ ] Full stack trace logged server-side only
- [ ] `str(e)` removed from all test endpoint error responses (`ui/app.py:366,397,425,486,573`)
- [ ] Generic messages like "Connection test failed" returned instead

**Files:** `server.py`, `ui/app.py`
**Scope:** S

### Task 9: CDN SRI & .env Hardening

**Description:** Add SRI hashes to CDN scripts, enforce .env file permissions.

**Acceptance criteria:**
- [ ] `base.html:7` Tailwind CDN: add `integrity` and `crossorigin="anonymous"`
- [ ] `base.html:26-27` HTMX CDN: add `integrity` and `crossorigin="anonymous"`
- [ ] Startup check: if `.env` exists and permissions not `0o600`, log WARNING and attempt `chmod 600`

**Files:** `ui/templates/base.html`, `server.py` (startup check)
**Scope:** S

### Task 10: Tests & Verification

**Description:** Update all existing tests, add security-specific tests, run full verification.

**Acceptance criteria:**
- [ ] All 90 existing tests pass (updated for auth)
- [ ] New tests: auth login/logout flow, RBAC enforcement, XSS escaping, forbidden paths, webhook rejection, SSRF blocking
- [ ] Run `python -m pytest tests/ -v` - all green
- [ ] Manual verification checklist from task directive passes

**Files:** `tests/test_ui.py`, `tests/test_server.py`, `tests/test_auth.py` (new), `tests/test_llm_safety.py` (new)
**Scope:** M

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Breaking all 90 existing tests with auth | High | Task 3 updates all tests; run after each phase |
| `passlib` bcrypt compatibility | Low | Well-tested library, add to deps early |
| In-memory sessions lost on restart | Low | Acceptable for single-process; document limitation |
| HTML escaping breaks dashboard layout | Medium | Test with `<>` in repo names; verify Jinja2 autoescaping |
| SSRF validation blocks legitimate localhost Ollama | Medium | Allow `localhost` for Ollama only (configurable allowlist) |

## Open Questions

- Should Ollama `localhost:11434` be exempted from SSRF checks? (Recommend: yes, via config flag)
- Should the login page require `SECRET_KEY` to be set, or use a default? (Recommend: require it, fail startup if missing)
