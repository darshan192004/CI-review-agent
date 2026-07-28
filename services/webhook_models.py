from __future__ import annotations

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
    author: str = ""


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

    status = payload.get("status", "")
    conclusion = payload.get("conclusion", "")
    if conclusion == "success":
        status = "completed"
    elif conclusion in ("failure", "cancelled"):
        status = "completed"

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
        author=sender_data.get("login", ""),
    )


def _parse_forgejo_action_payload(payload: dict[str, Any]) -> WebhookEvent:
    run = payload.get("run", {})
    repo_data = run.get("repository") or {}
    trigger_user = run.get("trigger_user") or {}
    action = payload.get("action", "")

    run_id = str(run.get("id", ""))
    run_attempt = str(run.get("run_attempt", 1))
    branch = run.get("prettyref", "")
    commit_sha = run.get("commit_sha", "")
    status = run.get("status", "")
    title = run.get("title", "")

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
        author=trigger_user.get("login", ""),
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
        author=sender_data.get("login", ""),
    )


def parse_webhook_payload(
    platform: CIPlatform, payload: dict[str, Any]
) -> WebhookEvent:
    if platform == CIPlatform.FORGEJO:
        return parse_forgejo_payload(payload)
    return parse_github_workflow_run(payload)
