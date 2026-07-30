from __future__ import annotations

import logging
from typing import Any

from config import settings
from services.ci_client import CIClient, create_ci_client
from services.run_tracker import run_tracker

logger = logging.getLogger(__name__)


async def discover_repos(org: str, platform: str = "forgejo") -> list[str]:
    """One-time discovery of all repos accessible by the CI token.

    For orgs: lists repos under that org.
    For users: falls back to user-scoped repos (Forgejo only).
    Returns full names in 'owner/repo' format.
    """
    token = _get_token(platform)
    base_url = _get_base_url(platform)
    if not token:
        logger.warning("No token configured for platform %s", platform)
        return []

    client = create_ci_client(platform, token, base_url)
    try:
        repos = await client.list_repos(org)
        logger.info("Discovered %d repos for %s (%s)", len(repos), org, platform)
        return repos
    except Exception as e:
        logger.warning("Failed to discover repos for %s: %s", org, e)
        return []
    finally:
        await client.close()


async def get_known_repos() -> list[str]:
    """Get unique repository names from webhook activity."""
    all_runs = await run_tracker.get_all_runs()
    repos: list[str] = []
    seen: set[str] = set()
    for run in reversed(all_runs):
        repo = run.get("repository", "")
        if repo and repo not in seen:
            seen.add(repo)
            repos.append(repo)
    return repos


async def fetch_repo_run_history() -> int:
    """Fetch recent CI runs from the provider API and seed the run_tracker.

    Queries each discovered repo for its last 20 CI runs and records them
    in the local run_tracker database so the dashboard is not blank on first load.
    Returns the number of runs recorded.
    """
    org = settings.forgejo_org or settings.github_org or ""
    platform = "forgejo" if settings.forgejo_org else "github"
    if not org or not _get_token(platform):
        logger.info("No org or token configured — skipping history fetch")
        return 0

    repos = await discover_repos(org, platform)
    if not repos:
        logger.info("No repos discovered — skipping history fetch")
        return 0

    total = 0
    seen: set[str] = set()

    for repo_full_name in repos:
        try:
            parts = repo_full_name.split("/")
            if len(parts) != 2:
                continue
            owner, repo_name = parts

            token = _get_token(platform)
            base_url = _get_base_url(platform)
            client = create_ci_client(platform, token, base_url)
            try:
                runs = await client.list_runs(owner, repo_name, "", limit=20)
            finally:
                await client.close()

            for run in runs:
                run_id = str(run.get("id", ""))
                if not run_id:
                    continue
                key = f"{repo_full_name}:{run_id}"
                if key in seen:
                    continue
                seen.add(key)

                run_status = (
                    run.get("conclusion")
                    or run.get("status", "")
                )
                if run_status in ("cancelled", "skipped", ""):
                    continue

                run_attempt = str(run.get("run_attempt", 1))
                branch = (
                    run.get("head_branch")
                    or run.get("prettyref", "")
                )
                commit_sha = (
                    run.get("head_sha")
                    or run.get("commit_sha", "")
                )
                author = (
                    run.get("trigger_user", {}).get("login", "")
                    or run.get("triggering_actor", {}).get("login", "")
                    or run.get("actor", {}).get("login", "")
                )

                await run_tracker.record(
                    repository=repo_full_name,
                    run_id=run_id,
                    run_attempt=run_attempt,
                    status=run_status,
                    platform=platform,
                    branch=branch,
                    commit_sha=commit_sha,
                    author=author,
                )
                total += 1
        except Exception as e:
            logger.warning("Failed to fetch history for %s: %s", repo_full_name, e)
            continue

    logger.info("History fetch complete: %d runs recorded across %d repos", total, len(repos))
    return total


def _get_token(platform: str) -> str:
    if platform == "github":
        return settings.github_token
    return settings.forgejo_token


def _get_base_url(platform: str) -> str:
    if platform == "github":
        return "https://api.github.com"
    return settings.forgejo_base_url
