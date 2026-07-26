# CI Review Agent — REST API Reference

The CI Review Agent exposes webhooks and control REST APIs via FastAPI on port `8000`.

---

## 1. Webhook Handlers

### `POST /webhook/github`
Listens for GitHub Actions `workflow_run` webhook events.

* **Headers**:
  * `X-GitHub-Event`: `workflow_run`
  * `X-Hub-Signature-256`: *(Optional HMAC SHA256 signature)*
* **Payload**: GitHub Workflow Run JSON
* **Response Codes**:
  * `202 Accepted`: Webhook received and dispatched to background agent worker.
  * `200 OK`: Ignored event type (not `workflow_run`).
  * `401 Unauthorized`: Webhook HMAC signature verification failed.

---

### `POST /webhook/forgejo`
Listens for Forgejo / Gitea CI webhook events.

* **Headers**:
  * `X-Forgejo-Signature`: *(Optional HMAC signature)*
* **Payload**: Forgejo Webhook JSON
* **Response Codes**:
  * `202 Accepted`: Webhook received and dispatched.
  * `401 Unauthorized`: Signature verification failed.

---

## 2. Agent Management & Dashboard Endpoints

### `GET /`
Renders the primary executive dashboard HTML.

---

### `GET /config`
Renders the advanced agent configuration GUI.

---

### `GET /runs`
Renders the run history and filterable audit trail.

---

### `GET /api/dashboard/partial`
* **Response**: HTML snippet (Table + Out-of-band OOB Metric Elements).
* **Usage**: Polled by HTMX every 1s for real-time live dashboard sync.

---

### `POST /api/trigger-test-run`
Simulates a CI failure run in the `run_tracker` system for instant testing.

* **Response**:
  ```json
  {
    "ok": true,
    "run_id": "4229",
    "repository": "owner/ci-test-repo"
  }
  ```

---

## 3. Settings & Credentials API

### `GET /api/settings`
Returns redacted configuration settings.

---

### `PUT /api/settings`
Updates agent configuration settings and persists changes to `.env`.

* **Request Content-Type**: `application/json` or `application/x-www-form-urlencoded`
* **Response**:
  ```json
  {
    "saved": 5,
    "keys": ["llm_provider", "openai_model", "max_retry_attempts"]
  }
  ```

---

## 4. Connection Test APIs

### `POST /api/test/github`
Verifies configured GitHub personal token against `https://api.github.com/user`.

---

### `POST /api/test/forgejo`
Verifies configured Forgejo token and Base URL against `/api/v1/user`.

---

### `POST /api/test/ollama`
Pings local Ollama endpoint (`/api/tags`) and returns installed models.

---

### `POST /api/test/mcp`
Pings the Go Universal Messaging MCP server over stdio JSON-RPC transport.

---

### `POST /api/test/messaging`
Sends a test alert to the configured messaging platform (Mattermost / Slack / Discord).
