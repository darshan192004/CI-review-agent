# Task Checklist: Security Hardening & RBAC

## Phase 1: Authentication Foundation

- [ ] **Task 1:** Create `services/auth.py` — password hashing, session management, FastAPI dependencies
  - Files: `services/auth.py` (new), `pyproject.toml`
  - Scope: S | Depends: None

- [ ] **Task 2:** Create login page + `/login` GET/POST + `/logout` POST routes
  - Files: `ui/templates/login.html` (new), `ui/app.py`
  - Scope: S | Depends: Task 1

- [ ] **Task 3:** Protect all routes with auth dependencies
  - Files: `ui/app.py`, `server.py`, `tests/test_ui.py`, `tests/test_server.py`
  - Scope: M | Depends: Tasks 1, 2

### Checkpoint: After Phase 1
- [ ] Unauthenticated `GET /` redirects to `/login`
- [ ] `PUT /api/settings` as viewer returns 403
- [ ] All existing tests updated to pass with auth

---

## Phase 2: Security Remediation

- [ ] **Task 4:** LLM output validation + forbidden paths guard + branch isolation
  - Files: `nodes.py`, `services/git_manager.py`, `tests/test_llm_safety.py` (new)
  - Scope: M | Depends: None

- [ ] **Task 5:** Mandatory webhook HMAC verification at startup
  - Files: `server.py`, `tests/test_server.py`
  - Scope: S | Depends: None

- [ ] **Task 6:** XSS prevention — `html.escape()` on all f-string HTML, fix `showToast`
  - Files: `server.py`, `ui/app.py`, `ui/static/js/app.js`
  - Scope: S | Depends: None

- [ ] **Task 7:** SSRF protection + make `mcp_server_command` read-only via API
  - Files: `services/net_utils.py` (new), `ui/app.py`
  - Scope: S | Depends: None

- [ ] **Task 8:** Global exception handler + sanitize error responses
  - Files: `server.py`, `ui/app.py`
  - Scope: S | Depends: None

- [ ] **Task 9:** Add SRI hashes to CDN scripts + `.env` permission check
  - Files: `ui/templates/base.html`, `server.py`
  - Scope: S | Depends: None

### Checkpoint: After Phase 2
- [ ] Webhooks without signature rejected with 401/403
- [ ] `<script>alert(1)</script>` in repo name renders as plain text
- [ ] LLM trying to modify `.env` or `server.py` raises ValueError
- [ ] SSRF to `169.254.169.254` rejected
- [ ] API errors return generic messages

---

## Phase 3: Tests & Verification

- [ ] **Task 10:** Update all existing tests, add security tests, run full verification
  - Files: `tests/test_ui.py`, `tests/test_server.py`, `tests/test_auth.py` (new), `tests/test_llm_safety.py` (new)
  - Scope: M | Depends: All above

### Final Verification
- [ ] `python -m pytest tests/ -v` — all pass
- [ ] Accessing `/` without session -> redirect to `/login`
- [ ] Viewer viewing dashboard -> works; viewer calling `PUT /api/settings` -> 403
- [ ] Webhooks without `X-Hub-Signature-256` -> rejected when secret configured
- [ ] Commit messages with `<script>alert(1)</script>` render as plain text
- [ ] LLM output modifying `.env` or `auth.py` -> ValueError, commit cancelled
- [ ] No secrets in source code or git history
- [ ] All user input validated at system boundaries
- [ ] Security headers present (CSP, HSTS, etc.)
- [ ] Error responses don't expose internal details
- [ ] SSRF to internal services blocked
- [ ] LLM output validated and encoded before use
