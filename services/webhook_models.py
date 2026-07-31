from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class CIPlatform(str, Enum):
    GITHUB = "github"
    FORGEJO = "forgejo"


class WebhookAction(str, Enum):
    COMPLETED = "completed"
    SUCCESS = "success"
    FAILURE = "failure"


class WebhookRepository(BaseModel):
    full_name: str = Field(description="owner/repo format")
    html_url: str = ""
    default_branch: str = "main"


class WebhookSender(BaseModel):
    login: str = ""
    id: int | None = None
    avatar_url: str = ""


class WorkflowRunInfo(BaseModel):
    id: int | str = ""
    name: str = ""
    branch: str = ""
    head_sha: str = ""
    status: str = ""
    conclusion: str = ""
    run_number: int = 0
    html_url: str = ""


class WebhookEvent(BaseModel):
    platform: CIPlatform
    action: str = ""
    repository: WebhookRepository
    sender: WebhookSender
    branch: str = ""
    commit_sha: str = ""
    run_id: str = ""
    run_attempt: str = "1"
    status: str = ""
    conclusion: str = ""
    author: str = ""
    commit_author: str = ""
    commit_author_email: str = ""


def parse_forgejo_payload(payload: dict[str, Any]) -> WebhookEvent:
    # Forgejo natively sends ActionPayload for action_run_* events
    if payload.get("run") is not None:
        return _parse_forgejo_action_payload(payload)

    action = payload.get("action", "")
    repo_data = payload.get("repository", {})
    sender_data = payload.get("sender", {})

    branch = payload.get("ref_name", "")
    if not branch:
        ref = payload.get("ref", "")
        branch = (
            ref.replace("refs/heads/", "") if ref.startswith("refs/heads/") else ref
        )

    run_id = ""
    run_attempt = "1"
    workflow_data = payload.get("workflow")
    if isinstance(workflow_data, dict):
        run_id = str(workflow_data.get("id", ""))
        run_attempt = str(workflow_data.get("run_attempt", 1))
    if not run_id:
        run_id = str(payload.get("run_id", ""))
    if not run_id:
        action_runs = payload.get("action_runs")
        if isinstance(action_runs, list) and action_runs:
            run_id = str(action_runs[0].get("id", ""))
    if not run_id:
        workflow_run = payload.get("workflow_run")
        if isinstance(workflow_run, dict):
            run_id = str(workflow_run.get("id", ""))
            run_attempt = str(workflow_run.get("run_attempt", 1))

    head_sha = payload.get("sha", "")
    if not head_sha:
        workflow_data = payload.get("workflow", {})
        if isinstance(workflow_data, dict):
            head_sha = workflow_data.get("head_sha", "")
    if not head_sha:
        workflow_run = payload.get("workflow_run")
        if isinstance(workflow_run, dict):
            head_sha = workflow_run.get("head_sha", "")

    status = payload.get("status", "")
    conclusion = payload.get("conclusion", "")
    if not status and not conclusion:
        workflow_run = payload.get("workflow_run")
        if isinstance(workflow_run, dict):
            status = workflow_run.get("status", "")
            conclusion = workflow_run.get("conclusion", "")
    if conclusion == "success":
        if not status:
            status = "completed"
    elif conclusion in ("failure", "cancelled"):
        if not status:
            status = "completed"

    # Determine author with fallbacks: sender → trigger_user → workflow_run.sender (Forgejo action_run)
    author = sender_data.get("login", "")
    commit_author = ""
    commit_author_email = ""
    if not author:
        workflow_run = payload.get("workflow_run")
        if isinstance(workflow_run, dict):
            author = (
                workflow_run.get("trigger_user", {}).get("login", "")
                or workflow_run.get("sender", {}).get("login", "")
            )
            # Commit author info from workflow_run.head_commit for Forgejo action_run events
            head_commit = workflow_run.get("head_commit") or {}
            if isinstance(head_commit, dict):
                commit_author = head_commit.get("author", {}).get("name", "") or head_commit.get("committer", {}).get("name", "")
                commit_author_email = head_commit.get("author", {}).get("email", "") or head_commit.get("committer", {}).get("email", "")
    # If author is still missing, check action.run object (Forgejo action_run payload)
    if not author:
        action_run = payload.get("run") or {}
        if isinstance(action_run, dict):
            trigger_user = action_run.get("trigger_user") or {}
            author = trigger_user.get("login", "")
            # Extract commit info from Forgejo action_run payload (inherently more robust for bot commits)
            head_commit = action_run.get("head_commit") or {}
            if isinstance(head_commit, dict):
                commit_author = head_commit.get("author", {}).get("name", "") or head_commit.get("committer", {}).get("name", "")
                commit_author_email = head_commit.get("author", {}).get("email", "") or head_commit.get("committer", {}).get("email", "")

    # Extract commit details from Forgejo action_run payload (which has richer commit info)
    action_run = payload.get("run") or {}
    if isinstance(action_run, dict):
        head_commit_obj = action_run.get("head_commit") or {}
        if isinstance(head_commit_obj, dict):
            commit_author = commit_author or head_commit_obj.get("author", {}).get("name", "") or head_commit_obj.get("committer", {}).get("name", "")
            commit_author_email = commit_author_email or head_commit_obj.get("author", {}).get("email", "") or head_commit_obj.get("committer", {}).get("email", "")

    return WebhookEvent(
        platform=CIPlatform.FORGEJO,
        action=action,
        repository=WebhookRepository(
            full_name=repo_data.get("full_name", ""),
            html_url=repo_data.get("html_url", ""),
            default_branch=repo_data.get("default_branch", "main"),
        ),
        sender=WebhookSender(
            login=sender_data.get("login", ""),
            id=sender_data.get("id"),
            avatar_url=sender_data.get("avatar_url", ""),
        ),
        branch=branch,
        commit_sha=head_sha,
        run_id=run_id,
        run_attempt=run_attempt,
        status=status,
        conclusion=conclusion,
        author=author,
        commit_author=commit_author,
        commit_author_email=commit_author_email,
    )


def _extract_commit_author_from_action_run(run: dict[str, Any]) -> tuple[str, str]:
    """Best-effort extraction of the triggering commit's author from a Forgejo
    action_run payload.

    The ActionRun struct (modules/structs/action.go) has no `head_commit`
    field; the raw push payload (including head_commit.author) is serialized as
    a JSON string in `run.event_payload`. The pusher account is in
    `run.trigger_user.login` — NOT the commit author, so it is unsuitable for
    bot detection.
    """
    event_payload = run.get("event_payload")
    if isinstance(event_payload, str) and event_payload.strip():
        try:
            push_data = json.loads(event_payload)
        except (ValueError, TypeError):
            push_data = {}
    else:
        push_data = {}
    head_commit = push_data.get("head_commit") or {}
    if isinstance(head_commit, dict):
        author = head_commit.get("author") or {}
        if isinstance(author, dict):
            return author.get("name", "") or "", author.get("email", "") or ""
    return "", ""


def _parse_forgejo_action_payload(payload: dict[str, Any]) -> WebhookEvent:
    run = payload.get("run", {})
    repo_data = run.get("repository") or {}
    trigger_user = run.get("trigger_user") or {}
    action = payload.get("action", "")

    run_id = str(run.get("id", "")) or str(payload.get("run_id", ""))
    run_attempt = str(run.get("run_attempt", 1)) or str(payload.get("run_attempt", "1"))
    branch = run.get("prettyref", "") or repo_data.get("default_branch", "main")
    commit_sha = run.get("commit_sha", "") or payload.get("sha", "")
    if not commit_sha:
        workflow_data = payload.get("workflow", {})
        if isinstance(workflow_data, dict):
            commit_sha = workflow_data.get("head_sha", "")
    status = run.get("status", "") or action

    commit_author, commit_author_email = _extract_commit_author_from_action_run(run)

    return WebhookEvent(
        platform=CIPlatform.FORGEJO,
        action=action,
        repository=WebhookRepository(
            full_name=repo_data.get("full_name", ""),
            html_url=repo_data.get("html_url", ""),
            default_branch=repo_data.get("default_branch", "main"),
        ),
        sender=WebhookSender(
            login=trigger_user.get("login", ""),
            id=trigger_user.get("id"),
            avatar_url=trigger_user.get("avatar_url", ""),
        ),
        branch=branch,
        commit_sha=commit_sha,
        run_id=run_id,
        run_attempt=run_attempt,
        status=status,
        conclusion=action,
        author=trigger_user.get("login", ""),
        commit_author=commit_author,
        commit_author_email=commit_author_email,
    )


def parse_github_workflow_run(payload: dict[str, Any]) -> WebhookEvent:
    action = payload.get("action", "")
    workflow_run = payload.get("workflow_run", {})
    repo_data = payload.get("repository", {})
    sender_data = payload.get("sender", {})

    branch = workflow_run.get("head_branch", "")
    commit_sha = workflow_run.get("head_sha", "")
    run_id = str(workflow_run.get("id", ""))
    run_attempt = str(workflow_run.get("run_attempt", 1))
    status = workflow_run.get("status", "")
    conclusion = workflow_run.get("conclusion", "")

    return WebhookEvent(
        platform=CIPlatform.GITHUB,
        action=action,
        repository=WebhookRepository(
            full_name=repo_data.get("full_name", ""),
            html_url=repo_data.get("html_url", ""),
            default_branch=repo_data.get("default_branch", "main"),
        ),
        sender=WebhookSender(
            login=sender_data.get("login", ""),
            id=sender_data.get("id"),
            avatar_url=sender_data.get("avatar_url", ""),
        ),
        branch=branch,
        commit_sha=commit_sha,
        run_id=run_id,
        run_attempt=run_attempt,
        status=status,
        conclusion=conclusion,
        author=sender_data.get("login", ""),
    )


def parse_webhook_payload(
    platform: CIPlatform, payload: dict[str, Any]
) -> WebhookEvent:
    if platform == CIPlatform.FORGEJO:
        return parse_forgejo_payload(payload)
    return parse_github_workflow_run(payload)
