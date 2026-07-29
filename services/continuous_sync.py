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


async def backfill_org_runs(
    org: str,
    platform: str = "forgejo",
    limit: int = 50,
    concurrency: int = 5,
) -> int:
    """One-time backfill of recent runs for all repos in an org.

    Runs concurrently with a semaphore to avoid overloading the CI server.
    Returns the total number of runs synced.
    """
    repos = await discover_repos(org, platform)
    logger.info("Backfilling up to %d runs from %d repos in %s", limit, len(repos), org)

    sem = asyncio.Semaphore(concurrency)
    total = 0

    async def _backfill_one(repo: str) -> int:
        nonlocal total
        async with sem:
            try:
                owner, repo_name = repo.split("/", 1)
                ci_client = create_ci_client(
                    platform, settings.forgejo_token, settings.forgejo_base_url
                )
                try:
                    runs = await ci_client.list_runs(owner, repo_name, "main", limit)
                    synced = 0
                    for run in runs:
                        status = _map_forgejo_status(run.get("status", ""))
                        actor = run.get("trigger_user") or run.get("actor") or {}
                        author = (
                            actor.get("login", "unknown")
                            if isinstance(actor, dict)
                            else str(actor)
                        )
                        dup = await run_tracker.is_duplicate(
                            repo, str(run.get("id", ""))
                        )
                        if not dup:
                            await run_tracker.record(
                                repository=repo,
                                run_id=str(run.get("id", "")),
                                run_attempt="1",
                                status=status,
                                platform=platform,
                                branch=run.get("prettyref", "main"),
                                commit_sha=run.get("commit_sha", ""),
                                author=author,
                            )
                            synced += 1
                    if synced:
                        logger.info(
                            "Backfill: synced %d runs for %s", synced, repo
                        )
                    return synced
                finally:
                    await ci_client.close()
            except Exception as e:
                logger.warning("Backfill failed for %s: %s", repo, e)
                return 0

    tasks = [_backfill_one(repo) for repo in repos]
    results = await asyncio.gather(*tasks)
    total = sum(results)
    logger.info("Backfill complete: %d total runs synced from %s", total, org)
    return total


def stop_all_sync_tasks() -> None:
    """Stop all continuous sync tasks (called on server shutdown)."""
    for _key, task in _sync_tasks.items():
        if not task.done():
            task.cancel()
    _sync_tasks.clear()
    logger.info("Stopped all continuous sync tasks")


async def discover_repos(org: str, platform: str = "forgejo") -> list[str]:
    """Discover all repos in an org using the CI client."""
    if platform == "forgejo":
        client = create_ci_client(platform, settings.forgejo_token, settings.forgejo_base_url)
    else:
        client = create_ci_client(platform, settings.github_token, "")
    try:
        return await client.list_repos(org)
    finally:
        await client.close()


async def _org_sync_loop(
    org: str, branch: str, platform: str, interval: int
) -> None:
    """Periodically discover and start syncing all repos in an org."""
    logger.info("Starting org sync for %s (platform=%s, interval=%ds)", org, platform, interval)
    while True:
        try:
            repos = await discover_repos(org, platform)
            logger.info("Discovered %d repos in org %s", len(repos), org)
            started = 0
            for repo in repos:
                if start_continuous_sync(repo, branch):
                    started += 1
            if started > 0:
                logger.info("Started monitoring %d new repos in %s", started, org)
        except Exception as e:
            logger.error("Org sync failed for %s: %s", org, e)
        await asyncio.sleep(interval)


def start_org_sync(
    org: str,
    branch: str = "main",
    platform: str = "forgejo",
    interval: int = 300,
) -> bool:
    """Start background org-level repo discovery and monitoring."""
    key = f"org:{platform}:{org}"
    if key in _sync_tasks and not _sync_tasks[key].done():
        logger.info("Org sync already running for %s", org)
        return False
    task = asyncio.create_task(_org_sync_loop(org, branch, platform, interval))
    _sync_tasks[key] = task
    logger.info("Started org sync for %s", org)
    return True


def stop_org_sync(org: str, platform: str = "forgejo") -> bool:
    """Stop org-level repo discovery."""
    key = f"org:{platform}:{org}"
    task = _sync_tasks.pop(key, None)
    if task and not task.done():
        task.cancel()
        logger.info("Stopped org sync for %s", org)
        return True
    return False
