from __future__ import annotations

import logging
import time
from typing import Any

from config import settings
from services.ci_client import create_ci_client
from services.run_tracker import run_tracker
from services.webhook_models import parse_iso_timestamp

logger = logging.getLogger(__name__)

_DISCOVERY_CACHE_TTL_SECONDS = 60.0
_discovery_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}


def resolve_scope(platform: str) -> tuple[str, str]:
    """Resolve the effective discovery scope for a platform.

    Returns (mode, value) where mode is "auto" | "org" | "user" and value is
    the org name or username to query (empty for auto).

    Legacy compat: before discovery modes existed, an org was the only scope
    knob. If an org is configured and the admin hasn't explicitly configured
    discovery yet (first run), keep discovering that org so existing setups
    don't silently switch to the token owner's repos.
    """
    mode = (settings.forgejo_discovery_mode if platform == "forgejo" else settings.github_discovery_mode) or "auto"
    mode = mode.strip().lower()
    org = settings.forgejo_org if platform == "forgejo" else settings.github_org
    username = settings.forgejo_username if platform == "forgejo" else settings.github_username

    if mode == "org":
        return ("org", org) if org else ("auto", "")
    if mode == "user":
        return ("user", username) if username else ("auto", "")
    if org and settings.discovery_configured != "true":
        return ("org", org)
    return ("auto", "")


def is_discovery_configured() -> bool:
    return settings.discovery_configured == "true"


def clear_discovery_cache() -> None:
    _discovery_cache.clear()


async def discover_repos(platform: str, *, force: bool = False) -> dict[str, Any]:
    """Discover repos accessible by the CI token for one platform.

    Returns a structured result:
      {"status": "ok" | "not_configured" | "error",
       "repos": [...full names...],
       "detail": str,
       "configured": bool}

    Results are TTL-cached so repeated rescan triggers (page load, SSE
    reconnect, config save) don't hammer the provider API.
    """
    token = _get_token(platform)
    if not token:
        return {
            "status": "not_configured",
            "repos": [],
            "detail": f"No {platform.title()} token configured.",
            "configured": is_discovery_configured(),
        }

    mode, value = resolve_scope(platform)
    cache_key = (platform, mode, value)
    now = time.monotonic()
    if not force:
        hit = _discovery_cache.get(cache_key)
        if hit and now - hit[0] < _DISCOVERY_CACHE_TTL_SECONDS:
            return hit[1]

    base_url = _get_base_url(platform)
    client = create_ci_client(platform, token, base_url)
    try:
        repos = await client.list_repos(mode, value)
        result = {
            "status": "ok",
            "repos": repos,
            "detail": _scope_detail(platform, mode, value, len(repos)),
            "configured": is_discovery_configured(),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("Discovery failed for %s (%s/%s): %s", platform, mode, value, e)
        result = {
            "status": "error",
            "repos": [],
            "detail": f"Discovery failed: {e}",
            "configured": is_discovery_configured(),
        }
    finally:
        await client.close()

    _discovery_cache[cache_key] = (now, result)
    return result


def _scope_detail(platform: str, mode: str, value: str, count: int) -> str:
    if mode == "org":
        scope = f"org {value}"
    elif mode == "user":
        scope = f"user {value}"
    else:
        scope = "your account (auto)"
    return f"Discovered {count} repos from {platform} ({scope})."


async def get_known_repos() -> list[str]:
    """Get unique repository names from webhook activity (newest first)."""
    all_runs = await run_tracker.get_all_runs()
    repos: list[str] = []
    seen: set[str] = set()
    for run in all_runs:
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
    total = 0
    seen: set[str] = set()
    seed_ts = time.time()

    for platform in ("forgejo", "github"):
        result = await discover_repos(platform, force=True)
        repos = result["repos"]
        if not repos or not _get_token(platform):
            continue

        base_url = _get_base_url(platform)
        for repo_full_name in repos:
            try:
                parts = repo_full_name.split("/")
                if len(parts) != 2:
                    continue
                owner, repo_name = parts

                token = _get_token(platform)
                client = create_ci_client(platform, token, base_url)
                try:
                    runs = await client.list_runs(owner, repo_name, "", limit=20)
                finally:
                    await client.close()

                for index, run in enumerate(runs):
                    run_id = str(run.get("id", ""))
                    if not run_id:
                        continue
                    key = f"{repo_full_name}:{run_id}"
                    if key in seen:
                        continue
                    seen.add(key)

                    run_status = run.get("conclusion") or run.get("status", "")
                    if run_status in ("cancelled", "skipped", ""):
                        continue

                    run_attempt = str(run.get("run_attempt", 1))
                    branch = run.get("head_branch") or run.get("prettyref", "")
                    commit_sha = run.get("head_sha") or run.get("commit_sha", "")
                    author = (
                        run.get("trigger_user", {}).get("login", "")
                        or run.get("triggering_actor", {}).get("login", "")
                        or run.get("actor", {}).get("login", "")
                    )
                    created_at = parse_iso_timestamp(run.get("created_at", ""))
                    if created_at is None:
                        # Some providers (Forgejo's actions-runs API) omit
                        # created_at. Anchor each run to the seed moment, newest
                        # first, so the provider's ordering survives the
                        # created_at DESC sort instead of inverting it.
                        created_at = seed_ts - index

                    await run_tracker.record(
                        repository=repo_full_name,
                        run_id=run_id,
                        run_attempt=run_attempt,
                        status=run_status,
                        platform=platform,
                        branch=branch,
                        commit_sha=commit_sha,
                        author=author,
                        created_at=created_at,
                        force_created_at=True,
                    )
                    total += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to fetch history for %s: %s", repo_full_name, e)
                continue

    logger.info("History fetch complete: %d runs recorded", total)
    return total


def _get_token(platform: str) -> str:
    if platform == "github":
        return settings.github_token
    return settings.forgejo_token


def _get_base_url(platform: str) -> str:
    if platform == "github":
        return "https://api.github.com"
    return settings.forgejo_base_url
