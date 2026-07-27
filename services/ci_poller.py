from __future__ import annotations

import asyncio
import logging
from typing import Any

from config import settings
from services.ci_client import ForgejoCIClient, create_ci_client
from services.run_tracker import run_tracker

logger = logging.getLogger(__name__)


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


async def poll_active_runs() -> None:
    active = await run_tracker.get_active_runs()
    if not active:
        return

    for run in active:
        repository = run["repository"]
        run_id = run["run_id"]
        current_status = run["status"]

        if "/" not in repository:
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


async def start_ci_poller(interval_seconds: int = 30) -> None:
    logger.info("Starting CI status poller (interval=%ss)", interval_seconds)
    while True:
        try:
            await poll_active_runs()
        except Exception as e:
            logger.error("CI poller failed: %s", e)
        await asyncio.sleep(interval_seconds)
