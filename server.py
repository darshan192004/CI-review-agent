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
        sse_event_bus.put_nowait({
            "task_key": task_key,
            "status": status,
            "meta": meta,
        })
    except asyncio.QueueFull:
        logger.warning("SSE event queue full, dropping event for %s", task_key)


def _check_config_warnings() -> None:
    _DEFAULT_FORGEJO_URL = "https://forgejo.example.com"
    if not settings.forgejo_org and not settings.github_org:
        logger.warning(
            "No org configured (FORGEJO_ORG / GITHUB_ORG). "
            "Repo discovery is disabled. Set one in .env or the Configuration page."
        )
    if settings.forgejo_org and not settings.forgejo_token:
        logger.warning(
            "FORGEJO_ORG is set (%s) but FORGEJO_TOKEN is empty. "
            "Repo discovery will fail.", settings.forgejo_org
        )
    if settings.forgejo_base_url == _DEFAULT_FORGEJO_URL:
        logger.warning(
            "FORGEJO_BASE_URL is still the default (%s). "
            "Update it to your Forgejo instance URL.", _DEFAULT_FORGEJO_URL
        )
    if settings.github_org and not settings.github_token:
        logger.warning(
            "GITHUB_ORG is set (%s) but GITHUB_TOKEN is empty. "
            "Repo discovery will fail.", settings.github_org
        )


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    global _start_time
    _setup_logging()
    _start_time = time.monotonic()
    logger.info("CI Review Agent server starting")
    _check_config_warnings()
    await run_tracker.clear()

    try:
        yield
    finally:
        logger.info("CI Review Agent server shutting down")
        await run_tracker.clear()


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
        failed = counts.get("FAILED", 0) + counts.get("failed", 0) + counts.get("error", 0)
        uptime_seconds = time.monotonic() - _start_time if _start_time else 0
        if uptime_seconds >= 3600:
            uptime = f"{int(uptime_seconds // 3600)}h {int((uptime_seconds % 3600) // 60)}m"
        elif uptime_seconds >= 60:
            uptime = f"{int(uptime_seconds // 60)}m {int(uptime_seconds % 60)}s"
        else:
            uptime = f"{int(uptime_seconds)}s"
        payload = json.dumps({
            "active": active, "success": success, "failed": failed, "uptime": uptime,
        })
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
            html_row = (
                f'<tr id="run-{e(event_data["task_key"])}" hx-swap-oob="outerHTML">'
                f'<td class="font-mono text-xs text-indigo-400">{e(meta.get("repository", ""))}</td>'
                f'<td class="font-mono text-xs text-slate-400">#{e(meta.get("run_id", ""))}</td>'
                f'<td class="font-mono text-xs text-slate-400">#{e(meta.get("run_attempt", "1"))}</td>'
                f"<td>{_sse_status_badge(event_data['status'])}</td>"
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


def _sse_status_badge(status: str) -> str:
    dot_pulse = '<span class="status-dot status-dot-pulse bg-blue-400"></span>'
    dot_green = '<span class="status-dot bg-emerald-400"></span>'
    dot_red = '<span class="status-dot bg-rose-400"></span>'
    dot_amber = '<span class="status-dot bg-amber-400"></span>'

    if status == "AGENT_WORKING":
        return f'<span class="badge badge-blue">{dot_pulse}Agent Working</span>'
    if status == "RUNNING":
        return f'<span class="badge badge-blue">{dot_pulse}Running</span>'
    if status in ("PASSED", "success"):
        return f'<span class="badge badge-green">{dot_green}Passed</span>'
    if status in ("FAILED", "failed"):
        return f'<span class="badge badge-red">{dot_red}Failed</span>'
    if status == "EXHAUSTED":
        return f'<span class="badge badge-orange">{dot_amber}Exhausted</span>'
    if status == "error":
        return f'<span class="badge badge-orange">{dot_amber}Error</span>'
    return f'<span class="badge badge-purple">{html_mod.escape(status)}</span>'


@app.post("/webhook/forgejo")
async def handle_forgejo_webhook(request: Request) -> Response:
    # === INSTRUMENT: log raw request metadata ===
    raw_headers = dict(request.headers)
    safe_headers = {k: v for k, v in raw_headers.items() if k.lower() not in ("authorization", "cookie", "x-hub-signature-256", "x-forgejo-signature")}
    sig_header = raw_headers.get("X-Hub-Signature-256") or raw_headers.get("X-Hub-Signature") or raw_headers.get("X-Forgejo-Signature", "(none)")
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
        logger.warning("AUTH FAILURE: No signature header found (tried X-Hub-Signature-256, X-Hub-Signature, X-Forgejo-Signature)")
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
    _ALLOWED_FORGEJO_EVENTS = frozenset({
        "push",
        "action_run", "action_run_failure", "action_run_success", "action_run_recover",
        "workflow_run",
    })
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

    broadcast_event(
        task_key=f"{event.repository.full_name}:{event.run_id}:{event.run_attempt}",
        status=event.status or "processing",
        meta={
            "repository": event.repository.full_name,
            "run_id": event.run_id,
            "run_attempt": event.run_attempt,
            "platform": event.platform.value,
            "branch": event.branch,
            "commit_sha": event.commit_sha,
            "author": event.author,
        },
    )

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
        verify_github_signature(
            body, sig_256, sig_1, settings.github_webhook_secret
        )
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

    broadcast_event(
        task_key=f"{event.repository.full_name}:{event.run_id}:{event.run_attempt}",
        status=event.status or "processing",
        meta={
            "repository": event.repository.full_name,
            "run_id": event.run_id,
            "run_attempt": event.run_attempt,
            "platform": event.platform.value,
            "branch": event.branch,
            "commit_sha": event.commit_sha,
            "author": event.author,
        },
    )

    return Response(status_code=202, content="Accepted")
