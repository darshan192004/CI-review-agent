from __future__ import annotations

import asyncio
import logging
import traceback

from config import settings
from main import run_agent
from services.ci_client import create_ci_client
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
    event_status = event.status or "processing"

    logger.info(
        "=== HANDLE WEBHOOK EVENT === repo=%s run_id=%s run_attempt=%s action=%s status=%s branch=%s sha=%s author=%s",
        repository,
        run_id,
        run_attempt,
        event.action,
        event.status,
        event.branch,
        event.commit_sha,
        event.author,
    )

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

    # Bot detection: skip events triggered by the bot itself.
    # The commit author (name/email) is the reliable discriminator — event.author
    # is the *pusher* account, which is identical for human and bot commits.
    commit_author = event.commit_author
    commit_author_email = event.commit_author_email
    has_configured_token = (
        isinstance(getattr(settings, "forgejo_token", None), str)
        and isinstance(getattr(settings, "forgejo_base_url", None), str)
    ) or isinstance(getattr(settings, "github_token", None), str)
    if not commit_author and not commit_author_email and event.commit_sha and has_configured_token:
        ci_client = None
        try:
            if event.platform.value == "forgejo":
                ci_client = create_ci_client(
                    "forgejo", settings.forgejo_token, settings.forgejo_base_url
                )
            else:
                ci_client = create_ci_client("github", settings.github_token, "")
            parts = repository.split("/")
            if len(parts) == 2:
                commit_author, commit_author_email = await ci_client.get_commit_author(
                    parts[0], parts[1], event.commit_sha
                )
                logger.info(
                    "Resolved commit author via API for %s: author=%s email=%s",
                    event.commit_sha,
                    commit_author,
                    commit_author_email,
                )
        except Exception as e:
            logger.warning(
                "Failed to resolve commit author via API for %s: %s",
                event.commit_sha,
                e,
            )
        finally:
            if ci_client is not None:
                await ci_client.close()

    is_bot_sender = bool(
        settings.ci_bot_username
        and event.author
        and event.author == settings.ci_bot_username
    )
    is_bot_commit = bool(
        (
            settings.ci_bot_username
            and commit_author
            and commit_author == settings.ci_bot_username
        )
        or (
            settings.ci_bot_email
            and commit_author_email
            and commit_author_email == settings.ci_bot_email
        )
    )
    if is_bot_sender or is_bot_commit:
        logger.info(
            "Event from bot — sender='%s' commit_author='%s' "
            "commit_author_email='%s' for %s run %s",
            event.author,
            event.commit_author,
            event.commit_author_email,
            repository,
            run_id,
        )
        # Match bot commit to the active session that pushed it
        session = await run_tracker.get_session_by_fix_sha(repository, event.commit_sha)
        if session:
            if event_status in ("success", "PASSED"):
                logger.info(
                    "Bot success for %s — completing session %d",
                    repository,
                    session["id"],
                )
                await run_tracker.update_session(
                    session["id"], status="PASSED",
                )
                await run_tracker.record(
                    repository, run_id, status="PASSED",
                    platform=event.platform.value,
                    branch=event.branch or event.repository.default_branch,
                    commit_sha=event.commit_sha,
                    author=event.author,
                )
                return
            elif event_status in ("failure", "FAILED", "error"):
                # Retry: match found, re-invoke agent with previous context
                if session["attempt_count"] >= session["max_attempts"]:
                    logger.info(
                        "Bot failure for %s — session %d exhausted "
                        "(attempt %d/%d)",
                        repository,
                        session["id"],
                        session["attempt_count"],
                        session["max_attempts"],
                    )
                    await run_tracker.update_session(
                        session["id"], status="EXHAUSTED",
                    )
                    await run_tracker.record(
                        repository, run_id, status="EXHAUSTED",
                        platform=event.platform.value,
                        branch=event.branch or event.repository.default_branch,
                        commit_sha=event.commit_sha,
                        author=event.author,
                    )
                    broadcast_event(
                        task_key=f"{repository}:{session['trigger_run_id']}:{run_attempt}",
                        status="EXHAUSTED",
                        meta={
                            "repository": repository,
                            "run_id": session["trigger_run_id"],
                            "exhausted_at": session["attempt_count"],
                        },
                    )
                    return
                # Continue to agent invocation below with session context
                logger.info(
                    "Bot failure for %s — retrying via session %d "
                    "(attempt %d/%d)",
                    repository,
                    session["id"],
                    session["attempt_count"],
                    session["max_attempts"],
                )
            else:
                # Bot RUNNING/PENDING/QUEUED — just record and wait
                await run_tracker.record(
                    repository, run_id, status=event_status,
                    platform=event.platform.value,
                    branch=event.branch or event.repository.default_branch,
                    commit_sha=event.commit_sha,
                    author=event.author,
                )
                return
        else:
            logger.info(
                "Bot commit with no matching session for %s run %s — "
                "skipping to prevent infinite loop",
                repository,
                run_id,
            )
            await run_tracker.record(
                repository,
                run_id,
                run_attempt=run_attempt,
                status="skipped_bot",
                platform=event.platform.value,
                branch=event.branch or event.repository.default_branch,
                commit_sha=event.commit_sha,
                author=event.author,
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

    # Only invoke the agent on actual CI failures
    # Forgejo action payloads: action field is the status (failure/success)
    # Forgejo/GitHub workflow_run events: action="completed" + conclusion="failure"
    is_failure = (
        event.action == "failure"
        or event.status in ("failure", "FAILED", "error")
        or (event.action == "completed" and event.conclusion == "failure")
        or (event.action == "completed" and not event.conclusion)
    )
    if not is_failure:
        logger.info(
            "Skipping agent — event is not a CI failure (action=%s, conclusion=%s, status=%s) "
            "for repo=%s run=%s.",
            event.action,
            event.conclusion,
            event.status,
            repository,
            run_id,
        )
        broadcast_event(
            task_key=f"{repository}:{run_id}:{run_attempt}",
            status=event_status,
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

    # Session management: create new session for human failures,
    # or reuse existing session for bot-retried failures.
    existing_session = await run_tracker.get_session_by_head_sha(repository, event.commit_sha)
    session = existing_session or await run_tracker.create_session(
        repository,
        event.commit_sha,
        branch=event.branch or event.repository.default_branch,
        trigger_run_id=run_id,
        max_attempts=settings.max_retry_attempts,
    )

    attempt_count = (session["attempt_count"] if session else 1)
    previous_context = session.get("previous_analysis", "") if session else ""

    initial_state: AgentState = {
        "repository": repository,
        "branch": event.branch or event.repository.default_branch,
        "commit_sha": event.commit_sha,
        "ci_platform": event.platform.value,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "attempt_count": attempt_count,
        "ci_status": "RUNNING",
        "failed_logs": "",
        "llm_analysis": "",
        "explanation": "",
        "patch_applied": False,
        "repo_info": {},
        "notifications_sent": [],
        "source_files": {},
        "ci_author": event.author,
        "commit_author": commit_author,
        "commit_author_email": commit_author_email,
        "failure_summary": "",
        "patch_summary": "",
        "previous_context": previous_context,
        "session_id": session["id"] if session else None,
    }

    await run_tracker.update_status(
            repository,
            run_id,
            status="AGENT_WORKING",
            run_attempt=run_attempt,
            attempt_count=0,
            branch=event.branch or event.repository.default_branch,
            commit_sha=event.commit_sha,
            author=event.author,
        )

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
        logger.info(
            "=== INVOKING LANGGRAPH AGENT === repo=%s run_id=%s platform=%s state_keys=%s",
            repository,
            run_id,
            event.platform.value,
            list(initial_state.keys()),
        )
        result = await run_agent(initial_state, use_dev_checkpointer=True)
        final_status = result.get("ci_status", "UNKNOWN")
        logger.info(
            "=== AGENT RETURNED === repo=%s run_id=%s final_status=%s result_keys=%s",
            repository,
            run_id,
            final_status,
            list(result.keys()),
        )
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
            attempt_count=result.get("attempt_count", 0),
        )
        # Update the retry session: record the fix SHA and analysis
        # for the next bot-triggered retry webhook.
        session_id = initial_state.get("session_id")
        if session_id and final_status == "FIX_PUSHED":
            await run_tracker.update_session(
                session_id,
                last_fix_sha=result.get("commit_sha", ""),
                previous_analysis=result.get("explanation", ""),
                attempt_count=result.get("attempt_count", attempt_count),
            )
        elif session_id and final_status in ("PASSED", "EXHAUSTED", "CANNOT_FIX"):
            await run_tracker.update_session(
                session_id, status=final_status,
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
        logger.error("Agent failed for %s run %s: %s\n%s", repository, run_id, e, traceback.format_exc())
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

    try:
        task = asyncio.create_task(handle_webhook_event(event))
        _active_tasks[task_key] = task
        logger.info("Dispatched background task for %s", task_key)
    except Exception as e:
        logger.error("Failed to create background task for %s: %s", task_key, e)


def get_active_task_count() -> int:
    return sum(1 for t in _active_tasks.values() if not t.done())

