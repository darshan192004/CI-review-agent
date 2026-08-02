# ADR-003: Model Context Protocol (MCP) for Universal Messaging Integration

## Status
**Superseded by ADR-006** — see note below.

> **Superseded (2026-08-02):** The MCP messaging subprocess was removed in favor of
> in-process notification delivery ([ADR-006](ADR-006-In-Process-Messaging-Notifications.md),
> implemented in `services/messaging/`). `services/mcp_client.py`, `mcp_servers/`,
> the `mcp` dependency, and the `/api/test/mcp` endpoint were deleted. Notifications are now
> sent directly via `httpx` with a `notification_trigger_level` gate
> (`always` / `failures_only` / `success_only` / `never`), and Telegram was added as a channel.
> The original rationale is retained below for historical context.

## Date
2026-07-26

## Context
When CI runs fail or succeed after self-healing attempts, the agent must notify team members across various chat platforms (Mattermost, Slack, Discord).

Requirements:
- Decouple notification dispatch logic from the core Python workflow engine.
- Support multiple messaging protocols seamlessly without writing separate HTTP client integration logic for each platform.
- Standardize alert formats (incident title, root cause analysis, resolution steps, status badges).

## Decision
Use an external **Universal Messaging MCP Server** (Go binary communicating via JSON-RPC stdio transport) invoked by the agent.

## Alternatives Considered

### Direct In-process HTTP Clients (`httpx`) per Platform
- **Pros**: Direct HTTP POST requests without external subprocesses.
- **Cons**: Tightly couples notification formatting and webhook schema variations to the core Python app; duplicate code for Mattermost, Slack, Discord, and Teams.
- **Rejected**: MCP provides a clean protocol abstraction for tool calling.

## Consequences
- The Python agent interacts with a standardized `send_alert` tool exposed via MCP over stdio JSON-RPC.
- Adding support for new communication channels (e.g. Teams, PagerDuty, Telegram) requires updating the MCP server without touching Python core code.
- Webhook URLs are passed safely to the subprocess via environment variables (`MATTERMOST_WEBHOOK_URL`, `SLACK_WEBHOOK_URL`, `DISCORD_WEBHOOK_URL`).
