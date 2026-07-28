from __future__ import annotations

import asyncio
import logging

from config import settings
from main import run_agent
from services.run_tracker import run_tracker
from services.webhook_models import WebhookEvent
from state import AgentState

logger = logging.getLogger(__name__)

_active_tasks: dict[str, asyncio.Task[None]] = {}


async def handle_webhook_event(event: WebhookEvent) -> None:
    from server import broadcast_event

    repository = event.repository.full_name
    run_id = event.run_id
    run_attempt = event.run_attempt

    if not run_id:
        logger.warning(
            "Webhook event missing run_id (repo=%s, action=%s), skipping",
            repository,
            event.action,
        )
        return

    if await run_tracker.is_duplicate(repository, run_id, run_attempt):
        logger.info(
            "Duplicate webhook for %s run %s attempt %s (action=%s), skipping",
            repository,
            run_id,
            run_attempt,
            event.action,
        )
        return

    # Detect rerun: run already completed, webhook is for a new attempt
    is_rerun = await run_tracker.is_completed(repository, run_id, run_attempt)
    if is_rerun:
        logger.info(
            "Rerun detected for %s run %s attempt %s",
            repository,
            run_id,
            run_attempt,
        )
        # Check if auto-fix on rerun is enabled
        if settings.auto_fix_reruns.lower() != "true":
            logger.info(
                "auto_fix_reruns is disabled — tracking status only for %s/%s",
                repository,
                run_id,
            )
            await run_tracker.record(
                repository,
                run_id,
                run_attempt=run_attempt,
                status=event.status or "processing",
                platform=event.platform.value,
                branch=event.branch or event.repository.default_branch,
                commit_sha=event.commit_sha,
                author=event.author,
            )
            # Broadcast the status update
            broadcast_event(
                task_key=f"{repository}:{run_id}:{run_attempt}",
                status=event.status or "processing",
                meta={
                    "repository": repository,
                    "run_id": run_id,
                    "run_attempt": run_attempt,
                    "platform": event.platform.value,
                    "branch": event.branch or event.repository.default_branch,
                    "commit_sha": event.commit_sha,
                    "author": event.author,
                },
            )
            return

    event_status = event.status or "processing"
    await run_tracker.record(
        repository,
        run_id,
        run_attempt=run_attempt,
        status=event_status,
        platform=event.platform.value,
        branch=event.branch or event.repository.default_branch,
        commit_sha=event.commit_sha,
        author=event.author,
    )

    source_files: dict[str, str] = {}

    initial_state: AgentState = {
        "repository": repository,
        "branch": event.branch or event.repository.default_branch,
        "commit_sha": event.commit_sha,
        "ci_platform": event.platform.value,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "attempt_count": 0,
        "ci_status": "RUNNING",
        "failed_logs": "",
        "llm_analysis": "",
        "explanation": "",
        "patch_applied": False,
        "repo_info": {},
        "notifications_sent": [],
        "source_files": source_files,
        "ci_author": event.author,
        "failure_summary": "",
        "patch_summary": "",
    }

    await run_tracker.update_status(repository, run_id, status="AGENT_WORKING", run_attempt=run_attempt)

    task_key = f"{repository}:{run_id}:{run_attempt}"
    broadcast_event(
        task_key=task_key,
        status="AGENT_WORKING",
        meta={
            "repository": repository,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "platform": event.platform.value,
            "branch": event.branch or event.repository.default_branch,
            "commit_sha": event.commit_sha,
            "author": event.author,
        },
    )

    try:
        result = await run_agent(initial_state, use_dev_checkpointer=True)
        final_status = result.get("ci_status", "UNKNOWN")
        await run_tracker.update_status(
            repository,
            run_id,
            run_attempt=run_attempt,
            status=final_status,
            branch=result.get("branch", initial_state.get("branch", "")),
            commit_sha=result.get("commit_sha", initial_state.get("commit_sha", "")),
            author=result.get("ci_author", event.author),
            failure_summary=result.get("failure_summary", ""),
            patch_summary=result.get("patch_summary", ""),
        )
        broadcast_event(
            task_key=task_key,
            status=final_status,
            meta={
                "repository": repository,
                "run_id": run_id,
                "run_attempt": run_attempt,
                "platform": event.platform.value,
                "branch": result.get("branch", initial_state.get("branch", "")),
                "commit_sha": result.get("commit_sha", initial_state.get("commit_sha", "")),
                "author": result.get("ci_author", event.author),
                "failure_summary": result.get("failure_summary", ""),
                "patch_summary": result.get("patch_summary", ""),
            },
        )
        logger.info(
            "Agent completed for %s run %s attempt %s: %s",
            repository,
            run_id,
            run_attempt,
            final_status,
        )
    except Exception as e:
        logger.error("Agent failed for %s run %s: %s", repository, run_id, e)
        await run_tracker.update_status(repository, run_id, status="error", run_attempt=run_attempt)
        broadcast_event(
            task_key=task_key,
            status="error",
            meta={
                "repository": repository,
                "run_id": run_id,
                "run_attempt": run_attempt,
                "platform": event.platform.value,
                "branch": event.branch or event.repository.default_branch,
                "commit_sha": event.commit_sha,
                "author": event.author,
                "failure_summary": str(e)[:200],
            },
        )
    finally:
        _active_tasks.pop(task_key, None)


def dispatch_webhook_event(event: WebhookEvent) -> None:
    task_key = f"{event.repository.full_name}:{event.run_id}:{event.run_attempt}"

    if task_key in _active_tasks and not _active_tasks[task_key].done():
        logger.warning("Task %s already running, skipping", task_key)
        return

    task = asyncio.create_task(handle_webhook_event(event))
    _active_tasks[task_key] = task
    logger.info("Dispatched background task for %s", task_key)


def get_active_task_count() -> int:
    return sum(1 for t in _active_tasks.values() if not t.done())
