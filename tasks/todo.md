# CI Review Agent - Implementation Checklist

## Phase 0: Core Agent ✅
- [x] `pyproject.toml` - dependencies
- [x] `config.py` - Pydantic settings
- [x] `state.py` - AgentState, enums, models
- [x] `services/log_parser.py` - ANSI strip, error extraction
- [x] `services/ci_client.py` - GitHub/Forgejo API client
- [x] `services/mcp_client.py` - MCP stdio client
- [x] `services/git_manager.py` - GitPython async wrapper
- [x] `nodes.py` - 5 graph nodes (fixed payloads)
- [x] `graph.py` - StateGraph with conditional edges
- [x] `main.py` - CLI entrypoint
- [x] Tests: 27 passing (log_parser, graph_routing, notification_payloads)

## Phase 1: Webhook Receiver Service
- [ ] Install dependencies: `fastapi`, `uvicorn`
- [ ] `services/webhook_models.py` - Forgejo/GitHub payload models
- [ ] `services/webhook_verify.py` - HMAC signature verification
- [ ] `services/run_tracker.py` - deduplication with TTL
- [ ] `server.py` - FastAPI app with webhook endpoints
- [ ] `services/webhook_handler.py` - background agent runner
- [ ] Health + status endpoints (`/health`, `/status`)
- [ ] Update `main.py` with `serve` subcommand
- [ ] Update `pyproject.toml` with fastapi/uvicorn deps
- [ ] Tests: webhook_models, webhook_verify, run_tracker
- [ ] Run full test suite

## Phase 2: Configuration UI
- [ ] Static file serving + Jinja2 templates
- [ ] Tailwind CSS + HTMX + Motion via CDN
- [ ] `templates/base.html` - base template
- [ ] `templates/dashboard.html` - overview page
- [ ] `templates/config.html` - settings form
- [ ] `templates/runs.html` - run history
- [ ] `GET /api/settings` + `PUT /api/settings`
- [ ] `POST /api/test/{github,forgejo,mcp,messaging}`
- [ ] `services/env_writer.py` - safe .env writer
- [ ] HTMX polling for live updates
- [ ] Motion animations

## Phase 3: Distribution
- [ ] `.env.example` with all variables
- [ ] `.gitignore`
- [ ] `Dockerfile` (multi-stage)
- [ ] `docker-compose.yml`
- [ ] `setup_wizard.py` (first-run CLI)
- [ ] Test `pip install` from GitHub
- [ ] Cross-platform testing (Windows, Linux, macOS)
- [ ] `README.md`

## Notes
- Motion = Framer Motion standalone vanilla JS (no React needed)
  CDN: `https://cdn.jsdelivr.net/npm/motion@latest/+esm`
- UI binds to `127.0.0.1` only (localhost, no auth needed)
- MCP server: `send_alert` tool with 4 required fields
  (platform, incident_title, root_cause, resolution_steps)
