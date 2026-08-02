from __future__ import annotations

import asyncio
import html as html_mod
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from services.auth import AuthRedirect, Forbidden
from services.ci_waiter import notify as ci_waiter_notify
from services.run_tracker import run_tracker
from services.webhook_handler import dispatch_webhook_event, get_active_task_count
from services.webhook_models import CIPlatform, parse_webhook_payload
from services.webhook_verify import (
    WebhookVerificationError,
    verify_forgejo_signature,
    verify_github_signature,
)
from ui.app import router as ui_router
from ui.badges import status_badge
from ui.formatters import format_run_time

logger = logging.getLogger(__name__)

_start_time: float = 0.0


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )


def _redact_error(msg: str) -> str:
    """Redact tokens, keys, and passwords from error messages."""
    msg = re.sub(r"(ghp_[A-Za-z0-9]{36})", r"[REDACTED]", msg)
    msg = re.sub(r"(github_pat_[A-Za-z0-9_]{82})", r"[REDACTED]", msg)
    msg = re.sub(r"(glpat-[A-Za-z0-9\-]{20,})", r"[REDACTED]", msg)
    msg = re.sub(r"(password|token|secret)[=:]\s*\S+", r"\1=[REDACTED]", msg, flags=re.IGNORECASE)
    msg = re.sub(r"(/[A-Za-z0-9_]+@github\.com)", r"/[REDACTED]@github.com", msg)
    return msg


# SSE event bus — broadcasts state changes to connected dashboard clients
sse_event_bus: asyncio.Queue[dict[str, Any]] = asyncio.Queue()


def broadcast_event(task_key: str, status: str, meta: dict[str, Any]) -> None:
    """Non-blocking put to the SSE event queue."""
    try:
        sse_event_bus.put_nowait(
            {
                "task_key": task_key,
                "status": status,
                "meta": meta,
            }
        )
    except asyncio.QueueFull:
        logger.warning("SSE event queue full, dropping event for %s", task_key)


def _format_uptime_hms(seconds: float) -> str:
    """Format an uptime duration as strict H:M:S (e.g. "0h 34m 5s")."""
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}h {minutes}m {secs}s"


async def _resolve_stored_run(meta: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve the ci_run row backing an SSE update.

    Session-bound events are keyed to a lifecycle row via session_id, so prefer
    ``get_run_by_session`` when available; fall back to the raw
    (repository, run_id, run_attempt) lookup otherwise.
    """
    session_id = meta.get("session_id")
    if session_id is not None:
        row = await run_tracker.get_run_by_session(meta.get("repository", ""), session_id)
        if row is not None:
            return row
    return await run_tracker.get_run(
        meta.get("repository", ""),
        meta.get("run_id", ""),
        meta.get("run_attempt", "1"),
    )


def _missing_llm_credential() -> tuple[str, str] | None:
    """Return (provider, human label) when the active LLM provider lacks its key."""
    provider = (settings.llm_provider or "").strip().lower()
    required_key = {
        "openai": "openai_api_key",
        "anthropic": "anthropic_api_key",
        "bedrock": "bedrock_aws_access_key_id",
        "azure_openai": "azure_openai_api_key",
        "gemini": "gemini_api_key",
        "mistral": "mistral_api_key",
        "cohere": "cohere_api_key",
        "groq": "groq_api_key",
        "together": "together_api_key",
        "deepseek": "deepseek_api_key",
        "xai": "xai_api_key",
    }.get(provider)
    if provider == "ollama":
        return None  # local endpoint, no API key required
    if required_key and not getattr(settings, required_key, ""):
        return provider, required_key.upper()
    return None


def _check_config_warnings() -> None:
    _DEFAULT_FORGEJO_URL = "https://forgejo.example.com"
    if not settings.forgejo_org and not settings.github_org:
        logger.warning(
            "No org configured (FORGEJO_ORG / GITHUB_ORG). "
            "Repo discovery is disabled. Set one in .env or the Configuration page."
        )
    missing_llm = _missing_llm_credential()
    if missing_llm:
        logger.warning(
            "LLM provider '%s' is selected but %s is empty - the agent will fail "
            "on the first analysis (runs end in 'error'). Set it in .env or the "
            "Configuration page.",
            missing_llm[0],
            missing_llm[1],
        )
    if settings.forgejo_org and not settings.forgejo_token:
        logger.warning(
            "FORGEJO_ORG is set (%s) but FORGEJO_TOKEN is empty. Repo discovery will fail.", settings.forgejo_org
        )
    if settings.forgejo_base_url == _DEFAULT_FORGEJO_URL:
        logger.warning(
            "FORGEJO_BASE_URL is still the default (%s). Update it to your Forgejo instance URL.", _DEFAULT_FORGEJO_URL
        )
    if settings.github_org and not settings.github_token:
        logger.warning(
            "GITHUB_ORG is set (%s) but GITHUB_TOKEN is empty. Repo discovery will fail.", settings.github_org
        )


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    global _start_time
    _setup_logging()
    _start_time = time.monotonic()
    logger.info("CI Review Agent server starting")
    _check_config_warnings()

    try:
        yield
    finally:
        logger.info("CI Review Agent server shutting down")


app = FastAPI(
    title="CI Review Agent",
    description="Webhook receiver for CI failure detection and automated fixing",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(AuthRedirect)
async def auth_redirect_handler(request: Request, exc: AuthRedirect) -> RedirectResponse:
    if request.headers.get("HX-Request") == "true":
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=401, content="", headers={"HX-Redirect": "/login"})
    return RedirectResponse(url="/login", status_code=302)


@app.exception_handler(Forbidden)
async def forbidden_handler(request: Request, exc: Forbidden) -> Response:
    return Response(status_code=403, content="Admin access required")


app.include_router(ui_router)

_UI_DIR = Path(__file__).resolve().parent / "ui"
app.mount("/static", StaticFiles(directory=str(_UI_DIR / "static")), name="ui-static")


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "healthy", "service": "ci-review-agent"}


@app.get("/status")
async def get_status() -> dict:
    uptime = time.monotonic() - _start_time if _start_time else 0
    counts = await run_tracker.count_by_status()
    active = await run_tracker.get_active_runs()
    return {
        "status": "running",
        "uptime_seconds": round(uptime, 1),
        "active_runs": len(active),
        "background_tasks": get_active_task_count(),
        "status_counts": counts,
    }


@app.get("/api/events")
async def sse_endpoint(request: Request, repo: str = Query("")) -> StreamingResponse:
    """Streams real-time CI run updates via Server-Sent Events."""

    async def _metrics_snapshot(filter_repo: str = "") -> str:
        if filter_repo:
            counts = await run_tracker.count_by_status(repo=filter_repo)
        else:
            counts = await run_tracker.count_by_status()
        active = counts.get("processing", 0) + counts.get("AGENT_WORKING", 0)
        success = counts.get("PASSED", 0) + counts.get("success", 0)
        failed = counts.get("FAILED", 0) + counts.get("failed", 0) + counts.get("failure", 0) + counts.get("error", 0)
        uptime_seconds = time.monotonic() - _start_time if _start_time else 0
        uptime = _format_uptime_hms(uptime_seconds)
        payload = json.dumps(
            {
                "active": active,
                "success": success,
                "failed": failed,
                "uptime": uptime,
            }
        )
        return f"event: metrics_update\ndata: {payload}\n\n"

    async def event_generator() -> AsyncIterator[str]:
        while True:
            if await request.is_disconnected():
                break
            try:
                event_data = await asyncio.wait_for(sse_event_bus.get(), timeout=1.0)
            except TimeoutError:
                yield await _metrics_snapshot(filter_repo=repo)
                continue

            meta = event_data.get("meta", {})
            e = html_mod.escape
            stored = await _resolve_stored_run(meta)
            run_time = format_run_time(stored["created_at"] if stored else 0)
            html_row = (
                f'<tr id="run-{e(event_data["task_key"])}" hx-swap-oob="outerHTML">'
                f'<td class="font-mono text-xs text-indigo-400">{e(meta.get("repository", ""))}</td>'
                f'<td class="font-mono text-xs text-slate-400">{e(run_time)}</td>'
                f'<td class="font-mono text-xs text-slate-400">#{e(meta.get("run_attempt", "1"))}</td>'
                f"<td>{status_badge(event_data['status'])}</td>"
                f'<td class="text-xs text-slate-400 uppercase font-mono">{e(meta.get("platform", ""))}</td>'
                f'<td class="text-xs text-slate-400">{e(meta.get("branch", ""))}</td>'
                f'<td class="text-xs text-slate-400 font-mono">{e((meta.get("commit_sha", "") or "")[:8])}</td>'
                f'<td class="text-xs text-slate-400">{e(meta.get("author", ""))}</td>'
                f"</tr>"
            )
            payload = json.dumps({"html": html_row, "task_key": event_data["task_key"], "status": event_data["status"]})
            yield f"event: ci_update\ndata: {payload}\n\n"
            # Also push a metrics snapshot after each CI event
            yield await _metrics_snapshot(filter_repo=repo)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/webhook/forgejo")
async def handle_forgejo_webhook(request: Request) -> Response:
    # === INSTRUMENT: log raw request metadata ===
    raw_headers = dict(request.headers)
    safe_headers = {
        k: v
        for k, v in raw_headers.items()
        if k.lower() not in ("authorization", "cookie", "x-hub-signature-256", "x-forgejo-signature")
    }
    sig_header = (
        raw_headers.get("X-Hub-Signature-256")
        or raw_headers.get("X-Hub-Signature")
        or raw_headers.get("X-Forgejo-Signature", "(none)")
    )
    event_header_raw = raw_headers.get("X-Forgejo-Event") or raw_headers.get("X-Gitea-Event") or "(none)"
    logger.info(
        "=== WEBHOOK RECEIVED === method=%s path=%s content_length=%s sig_header=%s event_header=%s headers=%s",
        request.method,
        request.url.path,
        request.headers.get("content-length", "?"),
        sig_header[:20] + "..." if len(sig_header) > 20 else sig_header,
        event_header_raw,
        safe_headers,
    )

    body = await request.body()
    logger.info("=== WEBHOOK BODY (first 500 bytes) === %s", body[:500])

    if not settings.forgejo_webhook_secret:
        logger.error("Forgejo webhook secret not configured — rejecting request")
        raise HTTPException(status_code=503, detail="Webhook secret not configured on server")

    # Accept standard X-Hub-Signature-256 (GitHub-compatible) or X-Forgejo-Signature
    signature = (
        request.headers.get("X-Hub-Signature-256")
        or request.headers.get("X-Hub-Signature")
        or request.headers.get("X-Forgejo-Signature")
    )
    if signature is None:
        logger.warning(
            "AUTH FAILURE: No signature header found (tried X-Hub-Signature-256, X-Hub-Signature, X-Forgejo-Signature)"
        )
    else:
        logger.info("AUTH: Using signature header (first 20 chars): %s...", signature[:20])
    try:
        verify_forgejo_signature(body, signature, settings.forgejo_webhook_secret)
    except WebhookVerificationError as e:
        logger.warning("AUTH FAILURE: Webhook signature mismatch: %s", e)
        raise HTTPException(status_code=401, detail=str(e))

    # Accept X-Forgejo-Event (custom) or X-Gitea-Event (Forgejo/Gitea native)
    event_type = request.headers.get("X-Forgejo-Event") or request.headers.get("X-Gitea-Event") or ""
    logger.info("EVENT: event_type=%s", event_type or "(empty — will be ignored)")
    _ALLOWED_FORGEJO_EVENTS = frozenset(
        {
            "push",
            "action_run",
            "action_run_failure",
            "action_run_success",
            "action_run_recover",
            "workflow_run",
        }
    )
    if event_type not in _ALLOWED_FORGEJO_EVENTS:
        logger.info("EVENT IGNORED: event_type=%s not in %s", event_type, _ALLOWED_FORGEJO_EVENTS)
        return Response(status_code=200, content="Ignored event type")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event = parse_webhook_payload(CIPlatform.FORGEJO, payload)

    logger.info(
        "Forgejo webhook: %s %s (repo=%s, run=%s)",
        event.action,
        event.status,
        event.repository.full_name,
        event.run_id,
    )

    # Notify CI waiter (push-based) if this is a terminal CI status
    ci_waiter_notify(
        repo=event.repository.full_name,
        commit_sha=event.commit_sha,
        status=event.status,
        run_id=event.run_id,
        run_attempt=event.run_attempt,
    )

    logger.info(
        "=== AGENT TRIGGER CHECK === action=%s status=%s platform=%s repo=%s run=%s — dispatching to handle_webhook_event",
        event.action,
        event.status,
        event.platform.value,
        event.repository.full_name,
        event.run_id,
    )
    dispatch_webhook_event(event)

    # SSE updates are emitted inside handle_webhook_event (background task) using
    # the session-bound lifecycle row key, so bot re-runs update the trigger row
    # instead of inserting a dangling row keyed by the bot's fresh run_id.
    return Response(status_code=202, content="Accepted")


@app.post("/webhook/github")
async def handle_github_webhook(request: Request) -> Response:
    body = await request.body()

    if not settings.github_webhook_secret:
        logger.error("GitHub webhook secret not configured — rejecting request")
        raise HTTPException(status_code=503, detail="Webhook secret not configured on server")

    sig_256 = request.headers.get("X-Hub-Signature-256")
    sig_1 = request.headers.get("X-Hub-Signature")
    try:
        verify_github_signature(body, sig_256, sig_1, settings.github_webhook_secret)
    except WebhookVerificationError as e:
        logger.warning("GitHub webhook verification failed: %s", e)
        raise HTTPException(status_code=401, detail=str(e))

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = request.headers.get("X-GitHub-Event", "")
    if event_type != "workflow_run":
        return Response(status_code=200, content="Ignored event type")

    event = parse_webhook_payload(CIPlatform.GITHUB, payload)

    logger.info(
        "GitHub webhook: %s %s (repo=%s, run=%s)",
        event.action,
        event.status,
        event.repository.full_name,
        event.run_id,
    )

    # Notify CI waiter (push-based) if this is a terminal CI status
    ci_waiter_notify(
        repo=event.repository.full_name,
        commit_sha=event.commit_sha,
        status=event.status,
        run_id=event.run_id,
        run_attempt=event.run_attempt,
    )

    logger.info(
        "=== AGENT TRIGGER CHECK === action=%s status=%s platform=%s repo=%s run=%s — dispatching to handle_webhook_event",
        event.action,
        event.status,
        event.platform.value,
        event.repository.full_name,
        event.run_id,
    )
    dispatch_webhook_event(event)

    # SSE updates are emitted inside handle_webhook_event (background task) using
    # the session-bound lifecycle row key, so bot re-runs update the trigger row
    # instead of inserting a dangling row keyed by the bot's fresh run_id.
    return Response(status_code=202, content="Accepted")
