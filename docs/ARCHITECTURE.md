# CI Review Agent — Architecture Overview

The **CI Review Agent** is an autonomous, self-healing CI/CD agent designed to automatically intercept build failures, analyze error tracebacks with Large Language Models, synthesize code patches, commit & re-test fixes, and notify engineering teams.

---

## High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                 CI Provider Webhook Event                   │
│               (GitHub Actions / Forgejo CI)                 │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                 FastAPI Web Server                          │
│  - Webhook Signature Verification (HMAC SHA256)             │
│  - RunTracker Deduplication                                 │
│  - Jinja2 + HTMX Live Management Dashboard                  │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                LangGraph Execution Engine                   │
│                     (StateGraph)                            │
│                                                             │
│  ┌──────────────┐     ┌──────────────┐     ┌─────────────┐  │
│  │  fetch_logs  ├────►│analyze_failure├────►│  apply_fix  │  │
│  └──────────────┘     └──────────────┘     └──────┬──────┘  │
│         ▲                                         │         │
│         │                                         ▼         │
│  ┌──────┴───────┐                          ┌─────────────┐  │
│  │  verify_ci   │◄─────────────────────────┤ commit_push │  │
│  └──────┬───────┘                          └─────────────┘  │
│         │                                                   │
│         ├─────────────► PASSED                              │
│         │                                                   │
│         ▼ (Attempts Exhausted)                              │
│  ┌──────────────┐                                           │
│  │send_notif... ├────► ESCALATE_HUMAN                       │
│  └──────────────┘                                           │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│          In-process Messaging (services/messaging/)          │
│   (Mattermost / Slack / Discord / Telegram Notifications)    │
│           gated by notification_trigger_level                │
└─────────────────────────────────────────────────────────────┘
```

---

## Core Components

### 1. Web Server & UI (`server.py`, `ui/app.py`)
* Built with **FastAPI** and **Uvicorn**.
* Features **HMAC Signature Verification** (`X-Hub-Signature-256`, `X-Forgejo-Signature`).
* Serves the management dashboard, configuration GUI, and run log history using Jinja2 templates, HTMX, and a custom high-speed CSS design system.

### 2. LangGraph Workflow Graph (`graph.py`, `nodes.py`, `state.py`)
* Maintains execution state across nodes using `AgentState`.
* **Nodes**:
  1. `fetch_logs`: Fetches workflow run logs from GitHub / Forgejo API.
  2. `analyze_failure`: Sends logs and source files to the configured LLM (OpenAI, Anthropic, Ollama, Azure OpenAI) to diagnose root cause and generate a patch diff.
  3. `apply_fix`: Validates and applies the patch diff using git tools.
  4. `verify_ci`: Commits fix, pushes branch, and polls CI runner to verify if tests pass.
  5. `send_notification`: Posts alert summary to the configured messaging channel in-process.

### 3. In-process Messaging (`services/messaging/`)
* Sends structured incident reports, patch summaries, and escalation alerts to Mattermost, Slack, or Discord webhooks, plus Telegram bots.
* Notification delivery is gated by `notification_trigger_level` (`always` / `failures_only` / `success_only` / `never`).

### 4. Configuration & State Persistence (`config.py`, `services/env_writer.py`)
* Settings load from `.env` file via `pydantic_settings`.
* Live configuration changes made via UI persist safely into `.env` without exposing masked secret values (`••••••••`).
