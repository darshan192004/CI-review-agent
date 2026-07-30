from __future__ import annotations

import asyncio
import logging
import time

from config import settings
from services.ci_client import create_ci_client
from services.run_tracker import run_tracker

logger = logging.getLogger(__name__)

# Adaptive polling intervals
ACTIVE_POLL_INTERVAL = 10  # seconds — for RUNNING / AGENT_WORKING runs
IDLE_POLL_INTERVAL = 60    # seconds — for completed/failed runs
WEBHOOK_STALE_THRESHOLD = 30  # seconds — skip poll if webhook arrived within this window


def _detect_platform(base_url: str) -> str:
    lower = base_url.lower()
    if "github.com" in lower:
        return "github"
    if "forgejo" in lower or "gitea" in lower or "localhost" in lower:
        return "forgejo"
    return "forgejo"


async def get_actual_ci_status(owner: str, repo: str, run_id: str) -> str | None:
    platform = _detect_platform(settings.forgejo_base_url)
    client = create_ci_client(
        platform=platform,
        token=settings.forgejo_token,
        base_url=settings.forgejo_base_url,
    )
    try:
        info = await client.get_run_info(owner, repo, run_id)
        status = info.get("status", "")
        conclusion = info.get("conclusion", "")

        if status == "completed":
            return "PASSED" if conclusion == "success" else "FAILED"
        if status:
            return {
                "failure": "FAILED",
                "cancelled": "FAILED",
                "skipped": "SKIPPED",
                "queued": "QUEUED",
                "pending": "PENDING",
                "running": "RUNNING",
                "waiting": "WAITING",
            }.get(status.lower(), status.upper())
        return None
    except Exception as e:
        logger.debug("Failed to fetch actual CI status for %s/%s/%s: %s", owner, repo, run_id, e)
        return None
    finally:
        await client.close()


def _is_active_status(status: str) -> bool:
    return status in ("RUNNING", "AGENT_WORKING", "processing", "PENDING", "QUEUED", "WAITING")


async def poll_active_runs() -> None:
    now = time.monotonic()
    runs = await run_tracker.get_pollable_runs(webhook_stale_seconds=WEBHOOK_STALE_THRESHOLD)
    if not runs:
        return

    for run in runs:
        repository = run["repository"]
        run_id = run["run_id"]
        current_status = run["status"]

        # Skip if webhook arrived recently
        last_webhook = run.get("last_webhook_at", 0)
        if last_webhook and (now - last_webhook) < WEBHOOK_STALE_THRESHOLD:
            logger.debug(
                "Skipping poll for %s/%s — webhook received %ds ago",
                repository, run_id, int(now - last_webhook),
            )
            continue

        if "/" not in repository:
            continue

        if current_status in ("AGENT_WORKING", "error", "EXHAUSTED", "PASSED", "success"):
            logger.debug(
                "Skipping poll for %s/%s — agent in control (status=%s)",
                repository, run_id, current_status,
            )
            continue

        owner, repo_name = repository.split("/", 1)
        actual = await get_actual_ci_status(owner, repo_name, run_id)

        if actual is None:
            continue

        if actual != current_status:
            logger.info(
                "Reconciling run %s/%s: agent=%s actual=%s",
                repository,
                run_id,
                current_status,
                actual,
            )
            await run_tracker.update_status(repository, run_id, status=actual)

            # Broadcast status change via SSE
            from server import broadcast_event
            broadcast_event(
                task_key=f"{repository}:{run_id}:{run.get('run_attempt', '1')}",
                status=actual,
                meta={
                    "repository": repository,
                    "run_id": run_id,
                    "run_attempt": run.get("run_attempt", "1"),
                    "platform": run.get("platform", ""),
                    "branch": run.get("branch", ""),
                    "commit_sha": run.get("commit_sha", ""),
                    "author": run.get("author", ""),
                },
            )


async def start_ci_poller(interval_seconds: int = ACTIVE_POLL_INTERVAL) -> None:
    logger.info(
        "Starting adaptive CI poller (active=%ds, idle=%ds, webhook_stale=%ds)",
        ACTIVE_POLL_INTERVAL,
        IDLE_POLL_INTERVAL,
        WEBHOOK_STALE_THRESHOLD,
    )
    while True:
        try:
            await poll_active_runs()
        except Exception as e:
            logger.error("CI poller failed: %s", e)
        await asyncio.sleep(interval_seconds)
