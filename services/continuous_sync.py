from __future__ import annotations

import asyncio
import logging
from typing import Any

from config import settings
from services.ci_client import create_ci_client
from services.run_tracker import run_tracker

logger = logging.getLogger(__name__)

# Active continuous sync tasks: repo_key -> asyncio.Task
_sync_tasks: dict[str, asyncio.Task[None]] = {}

SYNC_INTERVAL = 30  # seconds between polls


def _map_forgejo_status(status: str) -> str:
    return {
        "success": "PASSED",
        "failure": "FAILED",
        "running": "RUNNING",
        "waiting": "processing",
        "cancelled": "error",
        "skipped": "error",
    }.get(status, "processing")


async def _continuous_sync_loop(repo: str, branch: str, interval: int = SYNC_INTERVAL) -> None:
    """Background loop that continuously syncs runs from Forgejo."""
    owner, repo_name = repo.split("/", 1)
    logger.info("Starting continuous sync for %s (interval=%ds)", repo, interval)

    while True:
        try:
            ci_client = create_ci_client(
                "forgejo", settings.forgejo_token, settings.forgejo_base_url
            )
            try:
                runs = await ci_client.list_runs(owner, repo_name, branch, limit=20)
                new_count = 0
                for run in runs:
                    run_id = str(run.get("id", ""))
                    status = _map_forgejo_status(run.get("status", ""))
                    actor = run.get("trigger_user") or run.get("actor") or {}
                    author = actor.get("login", "unknown") if isinstance(actor, dict) else str(actor)

                    # Check if this is a new or updated run
                    existing_status = await run_tracker.get_run_status(repo, run_id)
                    if existing_status != status:
                        await run_tracker.record(
                            repository=repo,
                            run_id=run_id,
                            run_attempt="1",
                            status=status,
                            platform="forgejo",
                            branch=run.get("prettyref", branch),
                            commit_sha=run.get("commit_sha", ""),
                            author=author,
                        )
                        # Broadcast via SSE
                        from server import broadcast_event
                        broadcast_event(
                            task_key=f"{repo}:{run_id}:1",
                            status=status,
                            meta={
                                "repository": repo,
                                "run_id": run_id,
                                "run_attempt": "1",
                                "platform": "forgejo",
                                "branch": run.get("prettyref", branch),
                                "commit_sha": run.get("commit_sha", ""),
                                "author": author,
                            },
                        )
                        new_count += 1

                if new_count > 0:
                    logger.info("Continuous sync: %d runs updated for %s", new_count, repo)

            finally:
                await ci_client.close()

        except Exception as e:
            logger.error("Continuous sync failed for %s: %s", repo, e)

        await asyncio.sleep(interval)


def start_continuous_sync(repo: str, branch: str = "main") -> bool:
    """Start continuous sync for a repo. Returns True if started, False if already running."""
    key = f"{repo}:{branch}"
    if key in _sync_tasks and not _sync_tasks[key].done():
        logger.info("Continuous sync already running for %s", repo)
        return False

    task = asyncio.create_task(_continuous_sync_loop(repo, branch))
    _sync_tasks[key] = task
    logger.info("Started continuous sync for %s", repo)
    return True


def stop_continuous_sync(repo: str, branch: str = "main") -> bool:
    """Stop continuous sync for a repo. Returns True if stopped."""
    key = f"{repo}:{branch}"
    task = _sync_tasks.pop(key, None)
    if task and not task.done():
        task.cancel()
        logger.info("Stopped continuous sync for %s", repo)
        return True
    return False


def get_active_sync_tasks() -> list[dict[str, Any]]:
    """Get info about active continuous sync tasks."""
    active = []
    for key, task in _sync_tasks.items():
        repo, branch = key.rsplit(":", 1) if ":" in key else (key, "main")
        active.append({
            "repo": repo,
            "branch": branch,
            "running": not task.done(),
        })
    return active


def stop_all_sync_tasks() -> None:
    """Stop all continuous sync tasks (called on server shutdown)."""
    for _key, task in _sync_tasks.items():
        if not task.done():
            task.cancel()
    _sync_tasks.clear()
    logger.info("Stopped all continuous sync tasks")
