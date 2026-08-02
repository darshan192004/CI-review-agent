# ADR-006: In-Process Messaging Notifications

## Status
Accepted

## Date
2026-08-02

## Supersedes
[ADR-003: Model Context Protocol (MCP) for Universal Messaging Integration](ADR-003-Universal-Messaging-MCP-Integration.md)

## Context
CI failure/success/escalation alerts were previously dispatched through an external
Universal Messaging MCP Server (a Go subprocess speaking JSON-RPC over stdio).
This introduced an extra process, an external build artifact (`messaging-mcp`), and a
coupling to the `mcp` Python package for no functional benefit — every supported
platform (Mattermost, Slack, Discord) is a webhook POST, and Telegram uses a bot API POST.

Additionally, alerts fired unconditionally, so there was no way to restrict
notifications to failures only, successes only, or to disable them entirely.

## Decision
- Remove the MCP messaging subprocess entirely: `services/mcp_client.py`, `mcp_servers/`,
  the `mcp` dependency, and the `/api/test/mcp` endpoint.
- Deliver notifications in-process from `services/messaging/` using `httpx`:
  - `AlertPayload` (frozen dataclass): platform, incident title, root cause, resolution steps.
  - `BaseChannel` / `WebhookChannel` with Mattermost, Slack, Discord, and Telegram implementations.
  - `send_alert(platform, incident_title, root_cause, resolution_steps)` dispatches to the
    configured channel and raises `ValueError` for missing credentials.
- Gate every notification with `notification_trigger_level`:
  `always` / `failures_only` (default) / `success_only` / `never`.
- Add Telegram support via `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## Alternatives Considered

### Keep the MCP subprocess
- **Pros**: Protocol abstraction for tool calling; channel additions live in a separate binary.
- **Cons**: Extra subprocess lifecycle to manage, external build dependency, no in-process
  error propagation, and no native trigger-level gating. Rejected.

### Dedicated per-platform services with no shared channel abstraction
- **Pros**: Simplest possible code.
- **Cons**: Duplicated payload formatting and HTTP posting per platform. Rejected in favor of
  a small shared `BaseChannel` hierarchy with per-platform payload formatting.

## Consequences
- No `mcp` dependency and no `messaging-mcp` Go binary required to run the agent.
- Notifications are non-fatal: any `send_alert` failure is logged and never breaks the graph.
- Adding a new channel requires a `BaseChannel` subclass plus a `channel_config` entry.
- Webhook URLs and the Telegram bot token are passed to the channel constructors from
  pydantic settings (from `.env`); empty credentials raise `ValueError` at send time.
