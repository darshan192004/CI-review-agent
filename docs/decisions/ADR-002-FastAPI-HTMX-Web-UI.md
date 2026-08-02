# ADR-002: Fast API + Jinja2 + HTMX for Real-Time Management UI

## Status
Accepted

## Date
2026-07-26

## Context
The agent requires an intuitive, high-performance web interface for:
- Live monitoring of active and historical CI runs.
- Interactive configuration of credentials (tokens, webhooks, LLM models).
- Instant connection testing for GitHub, Forgejo, local Ollama, and messaging channels.

Requirements:
- Minimal footprint and zero build-step overhead.
- Instant rendering with zero client-side JIT lag.
- Real-time synchronous updates for dashboard metrics and tables without heavy SPA complexity (e.g. React/Vue).

## Decision
Use **FastAPI** coupled with **Jinja2 Templates**, **HTMX**, and a pre-compiled CSS design system ([`styles.css`](file:///E:/Ci-review-agent/ui/static/css/styles.css)).

## Alternatives Considered

### React / Next.js SPA
- **Pros**: Rich component ecosystem.
- **Cons**: High build complexity, separate Node.js build process, heavy bundle size, potential CORS overhead between backend and frontend.
- **Rejected**: Unnecessary complexity for a server-side Python tool.

### Pure Static HTML + REST Polling via Fetch API
- **Pros**: Standard browser APIs, no libraries.
- **Cons**: High manual DOM manipulation code, repetitive event listener logic for form submissions, partial updates, and out-of-band element syncing.
- **Rejected**: HTMX provides clean, declarative HTML attributes (`hx-get`, `hx-put`, `hx-swap-oob`) with minimal JavaScript.

### Client-side Tailwind JIT CDN Runtime (`cdn.tailwindcss.com`)
- **Pros**: Quick prototype utility classes.
- **Cons**: High browser CPU overhead due to DOM mutation observer parsing every element, causing visible layout shifts and UI response lag.
- **Rejected**: Replaced with a compiled CSS design system using native variables and utility classes.

## Consequences
- Fast server-side rendering with Jinja2.
- 1-second real-time live metric syncing using HTMX partial responses and Out-Of-Band (`hx-swap-oob`) element updates.
- Zero client build steps required — server can be started with a single `python main.py serve` command.
