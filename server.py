from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles

from config import settings
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


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    global _start_time
    _start_time = time.monotonic()
    logger.info("CI Review Agent server starting")
    yield
    logger.info("CI Review Agent server shutting down")
    run_tracker.clear()


app = FastAPI(
    title="CI Review Agent",
    description="Webhook receiver for CI failure detection and automated fixing",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(ui_router)

_UI_DIR = Path(__file__).resolve().parent / "ui"
app.mount("/static", StaticFiles(directory=str(_UI_DIR / "static")), name="ui-static")


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "healthy", "service": "ci-review-agent"}


@app.get("/status")
async def get_status() -> dict:
    uptime = time.monotonic() - _start_time if _start_time else 0
    counts = run_tracker.count_by_status()
    active = run_tracker.get_active_runs()
    return {
        "status": "running",
        "uptime_seconds": round(uptime, 1),
        "active_runs": len(active),
        "background_tasks": get_active_task_count(),
        "status_counts": counts,
    }


@app.post("/webhook/forgejo")
async def handle_forgejo_webhook(request: Request) -> Response:
    body = await request.body()

    if settings.forgejo_webhook_secret:
        signature = request.headers.get("X-Forgejo-Signature")
        try:
            verify_forgejo_signature(body, signature, settings.forgejo_webhook_secret)
        except WebhookVerificationError as e:
            logger.warning("Forgejo webhook verification failed: %s", e)
            raise HTTPException(status_code=401, detail=str(e))

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

    dispatch_webhook_event(event)

    return Response(status_code=202, content="Accepted")


@app.post("/webhook/github")
async def handle_github_webhook(request: Request) -> Response:
    body = await request.body()

    if settings.github_webhook_secret:
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

    dispatch_webhook_event(event)

    return Response(status_code=202, content="Accepted")
