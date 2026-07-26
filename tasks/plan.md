# CI Review Agent - 3-Phase Implementation Plan

## Overview

A production-ready, self-healing CI/CD agent using LangGraph that monitors CI failures, analyzes logs with LLM, applies patches, and notifies teams via MCP.

## Phase 1: Webhook Receiver Service

### Goal
Accept webhook callbacks from GitHub Actions / Forgejo CI when workflow runs complete, then trigger the existing LangGraph agent automatically.

### Tasks

1. **Webhook Payload Models** (`services/webhook_models.py`)
   - Pydantic models for Forgejo and GitHub `workflow_run` webhook payloads
   - Normalized `WebhookEvent` model for internal use
   - Extract: repository, branch, commit SHA, run ID, status, author, platform

2. **Webhook Signature Verification** (`services/webhook_verify.py`)
   - Forgejo: HMAC-SHA256 via `X-Forgejo-Signature` header
   - GitHub: HMAC-SHA256 via `X-Hub-Signature-256` header
   - Timing-safe comparison using `hmac.compare_digest()`
   - Source: Python stdlib `hmac` module, Forgejo webhook docs

3. **Run Tracking / Deduplication** (`services/run_tracker.py`)
   - In-memory dict tracking `(repo, run_id) -> status` to prevent re-processing
   - TTL-based expiry (1 hour default)
   - Thread-safe via `asyncio.Lock`

4. **FastAPI Webhook Endpoint** (`server.py`)
   - `POST /webhook/forgejo` - receives Forgejo webhook payloads
   - `POST /webhook/github` - receives GitHub webhook payloads
   - Reads raw body for signature verification, then parses JSON
   - Returns 202 Accepted immediately, processes in background
   - Source: FastAPI docs - Using Request Directly, Background Tasks

5. **Background Task Runner** (`services/webhook_handler.py`)
   - `asyncio.create_task()` for non-blocking agent execution
   - Normalizes webhook payload to `AgentState`
   - Calls `run_agent()` from `main.py`
   - Error handling and logging

6. **Health + Status Endpoints**
   - `GET /health` - liveness check (200 OK)
   - `GET /status` - active runs, completed count, error count
   - Startup/shutdown lifespan events for cleanup

7. **Update `main.py`** with `serve` / `run` modes
   - `ci-agent serve` - starts FastAPI server with uvicorn
   - `ci-agent run --repo ... --run-id ...` - existing CLI behavior
   - Argparse subcommands

### Dependencies Added
- `fastapi>=0.115.0`
- `uvicorn>=0.32.0`

## Phase 2: Configuration UI

### Goal
Web-based dashboard for configuring the agent, viewing runs, and testing connections.

### Tasks

8. **Static File Serving + Templates**
   - FastAPI `StaticFiles` mount for CSS/JS
   - Jinja2 `Templates` for HTML rendering
   - Tailwind CSS via CDN (no build step)
   - Motion (Framer Motion vanilla JS) via CDN for animations
   - HTMX via CDN for dynamic interactions
   - Source: FastAPI Static Files + Templates docs

9. **Settings API**
   - `GET /api/settings` - returns current `.env` values (redacted secrets)
   - `PUT /api/settings` - updates `.env` file
   - Pydantic models for settings validation

10. **Dashboard Page**
    - Overview: active runs, success/failure counts, last 10 runs
    - HTMX polling for live updates
    - Motion animations on status changes

11. **Configuration Page**
    - Forms for: CI tokens, LLM keys, MCP server, messaging webhooks
    - Connection test buttons (GitHub API, Forgejo API, MCP server)
    - Save to `.env` file

12. **Runs History Page**
    - Paginated list of all processed runs
    - Filter by: status, platform, repository
    - Click to expand: full logs, LLM analysis, patch diff

13. **Connection Test Endpoints**
    - `POST /api/test/github` - validates GitHub token
    - `POST /api/test/forgejo` - validates Forgejo token + base URL
    - `POST /api/test/mcp` - tests MCP server connectivity
    - `POST /api/test/messaging` - sends test notification

14. **Environment Writer** (`services/env_writer.py`)
    - Safely writes key=value pairs to `.env` file
    - Preserves comments and ordering
    - Atomic write (write to temp, rename)

## Phase 3: Distribution

### Goal
Make the agent installable via `pip install` and deployable via Docker.

### Tasks

15. **`.env.example`**
    - All environment variables with descriptions
    - Example values for non-sensitive settings

16. **`.gitignore`**
    - Python artifacts, `.env`, `__pycache__`, `*.db`
    - IDE files, OS files

17. **`Dockerfile`**
    - Multi-stage build: builder + runtime
    - Python 3.11-slim base
    - Copy Go MCP binary from builder stage
    - Expose port 8000
    - Source: FastAPI Docker deployment docs

18. **First-Run Setup Wizard** (`setup_wizard.py`)
    - Interactive CLI prompts for initial configuration
    - Generates `.env` file
    - Validates all tokens before completing

19. **`pip install` from GitHub**
    - Update `pyproject.toml` with all dependencies
    - Console script entry point: `ci-agent`
    - Test: `pip install git+https://github.com/...`

20. **Cross-Platform Testing**
    - Windows: Python 3.11+, PowerShell
    - Linux: Python 3.11+, Docker
    - macOS: Python 3.11+, Docker

21. **`README.md`**
    - Quick start (pip install + config + run)
    - Docker deployment
    - Configuration reference
    - Architecture diagram
    - Troubleshooting

## Architecture

```
Webhook (GitHub/Forgejo)
    |
    v
FastAPI Server (server.py)
    |
    +--> Signature Verification (webhook_verify.py)
    +--> Deduplication (run_tracker.py)
    +--> Background Task (webhook_handler.py)
              |
              v
         LangGraph Agent (graph.py)
              |
              +--> Fetch Logs (ci_client.py)
              +--> LLM Analysis (nodes.py)
              +--> Git Patch (git_manager.py)
              +--> Poll CI (ci_client.py)
              +--> Notify via MCP (mcp_client.py)
                      |
                      v
               MCP Server (Go binary)
                      |
                      v
               Mattermost / Slack / Discord
```
