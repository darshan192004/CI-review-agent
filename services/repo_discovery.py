from __future__ import annotations

import logging

from services.ci_client import create_ci_client
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


def _get_token(platform: str) -> str:
    from config import settings

    if platform == "github":
        return settings.github_token
    return settings.forgejo_token


def _get_base_url(platform: str) -> str:
    from config import settings

    if platform == "github":
        return "https://api.github.com"
    return settings.forgejo_base_url
