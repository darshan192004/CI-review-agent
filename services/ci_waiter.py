from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_futures: dict[str, asyncio.Future] = {}


def _key(repo: str, commit_sha: str) -> str:
    return f"{repo}:{commit_sha}"


async def wait_for_ci(
    repo: str, commit_sha: str, timeout: int = 600
) -> dict:
    key = _key(repo, commit_sha)
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    _futures[key] = future
    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        return result
    except asyncio.TimeoutError:
        logger.warning("Timeout waiting for CI webhook: %s @ %s", repo, commit_sha[:8])
        return {"ci_status": "TIMEOUT", "run_id": "", "run_attempt": "", "failed_logs": ""}
    finally:
        _futures.pop(key, None)


def notify(
    repo: str,
    commit_sha: str,
    status: str,
    run_id: str,
    run_attempt: str = "",
    failed_logs: str = "",
) -> bool:
    key = _key(repo, commit_sha)
    future = _futures.get(key)
    if future is None or future.done():
        return False
    future.set_result({
        "ci_status": status,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "failed_logs": failed_logs,
    })
    return True


def has_waiters(repo: str, commit_sha: str) -> bool:
    key = _key(repo, commit_sha)
    future = _futures.get(key)
    return future is not None and not future.done()
