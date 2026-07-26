from __future__ import annotations

import asyncio
import logging
from typing import Any

from main import run_agent
from services.run_tracker import run_tracker
from services.webhook_models import WebhookEvent
from state import AgentState

logger = logging.getLogger(__name__)

_active_tasks: dict[str, asyncio.Task[None]] = {}


async def handle_webhook_event(event: WebhookEvent) -> None:
    repository = event.repository.full_name
    run_id = event.run_id

    if not run_id:
        logger.warning("Webhook event missing run_id, skipping")
        return

    if run_tracker.is_duplicate(repository, run_id):
        logger.info("Duplicate webhook for %s run %s, skipping", repository, run_id)
        return

    run_tracker.record(repository, run_id, status="processing")

    source_files: dict[str, str] = {}

    initial_state: AgentState = {
        "repository": repository,
        "branch": event.branch or event.repository.default_branch,
        "commit_sha": event.commit_sha,
        "ci_platform": event.platform.value,
        "run_id": run_id,
        "attempt_count": 0,
        "ci_status": "RUNNING",
        "failed_logs": "",
        "llm_analysis": "",
        "patch_diff": "",
        "notifications_sent": [],
        "source_files": source_files,
        "ci_author": event.author,
        "failure_summary": "",
        "patch_summary": "",
    }

    task_key = f"{repository}:{run_id}"

    try:
        result = await run_agent(initial_state, use_dev_checkpointer=True)
        final_status = result.get("ci_status", "UNKNOWN")
        run_tracker.update_status(repository, run_id, status=final_status)
        logger.info(
            "Agent completed for %s run %s: %s",
            repository,
            run_id,
            final_status,
        )
    except Exception as e:
        logger.error("Agent failed for %s run %s: %s", repository, run_id, e)
        run_tracker.update_status(repository, run_id, status="error")
    finally:
        _active_tasks.pop(task_key, None)


def dispatch_webhook_event(event: WebhookEvent) -> None:
    task_key = f"{event.repository.full_name}:{event.run_id}"

    if task_key in _active_tasks and not _active_tasks[task_key].done():
        logger.warning("Task %s already running, skipping", task_key)
        return

    task = asyncio.create_task(handle_webhook_event(event))
    _active_tasks[task_key] = task
    logger.info("Dispatched background task for %s", task_key)


def get_active_task_count() -> int:
    return sum(1 for t in _active_tasks.values() if not t.done())
