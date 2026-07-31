from __future__ import annotations

import hashlib
import hmac
import json

from services.webhook_models import (
    CIPlatform,
    WebhookEvent,
    parse_forgejo_payload,
    parse_github_workflow_run,
    parse_webhook_payload,
)


FORGEJO_PAYLOAD: dict = {
    "action": "completed",
    "workflow": {
        "id": 12345,
        "name": "CI",
        "head_sha": "abc123def456",
    },
    "ref_name": "main",
    "sha": "abc123def456",
    "repository": {
        "full_name": "myorg/myrepo",
        "html_url": "https://forgejo.example.com/myorg/myrepo",
        "default_branch": "main",
    },
    "sender": {
        "login": "testuser",
        "id": 42,
        "avatar_url": "https://example.com/avatar.png",
    },
}

GITHUB_PAYLOAD: dict = {
    "action": "completed",
    "workflow_run": {
        "id": 67890,
        "name": "CI",
        "head_branch": "feature-x",
        "head_sha": "def789abc012",
        "status": "completed",
        "conclusion": "failure",
    },
    "repository": {
        "full_name": "octocat/Hello-World",
        "html_url": "https://github.com/octocat/Hello-World",
        "default_branch": "main",
    },
    "sender": {
        "login": "octocat",
        "id": 1,
        "avatar_url": "https://avatars.githubusercontent.com/u/1",
    },
}


class TestParseForgejoPayload:
    def test_basic_parse(self) -> None:
        event = parse_forgejo_payload(FORGEJO_PAYLOAD)
        assert event.platform == CIPlatform.FORGEJO
        assert event.repository.full_name == "myorg/myrepo"
        assert event.branch == "main"
        assert event.commit_sha == "abc123def456"
        assert event.run_id == "12345"
        assert event.author == "testuser"

    def test_fallback_ref_name(self) -> None:
        payload = {**FORGEJO_PAYLOAD, "ref_name": "", "ref": "refs/heads/develop"}
        event = parse_forgejo_payload(payload)
        assert event.branch == "develop"

    def test_empty_run_id_fallback(self) -> None:
        payload = {**FORGEJO_PAYLOAD, "workflow": {}, "run_id": "999"}
        event = parse_forgejo_payload(payload)
        assert event.run_id == "999"

    def test_success_conclusion(self) -> None:
        payload = {**FORGEJO_PAYLOAD, "conclusion": "success", "status": ""}
        event = parse_forgejo_payload(payload)
        assert event.status == "completed"

    def test_failure_conclusion(self) -> None:
        payload = {**FORGEJO_PAYLOAD, "conclusion": "failure", "status": ""}
        event = parse_forgejo_payload(payload)
        assert event.status == "completed"


class TestParseGithubPayload:
    def test_basic_parse(self) -> None:
        event = parse_github_workflow_run(GITHUB_PAYLOAD)
        assert event.platform == CIPlatform.GITHUB
        assert event.repository.full_name == "octocat/Hello-World"
        assert event.branch == "feature-x"
        assert event.commit_sha == "def789abc012"
        assert event.run_id == "67890"
        assert event.author == "octocat"
        assert event.status == "completed"

    def test_empty_workflow_run(self) -> None:
        payload = {
            "action": "completed",
            "workflow_run": {},
            "repository": {"full_name": "a/b", "default_branch": "main"},
            "sender": {"login": "u"},
        }
        event = parse_github_workflow_run(payload)
        assert event.run_id == ""
        assert event.branch == ""


class TestParseWebhookPayload:
    def test_forgejo_dispatch(self) -> None:
        event = parse_webhook_payload(CIPlatform.FORGEJO, FORGEJO_PAYLOAD)
        assert event.platform == CIPlatform.FORGEJO
        assert event.run_id == "12345"

    def test_github_dispatch(self) -> None:
        event = parse_webhook_payload(CIPlatform.GITHUB, GITHUB_PAYLOAD)
        assert event.platform == CIPlatform.GITHUB
        assert event.run_id == "67890"


FORGEJO_ACTION_PAYLOAD: dict = {
    "action": "failure",
    "run": {
        "id": 22,
        "run_attempt": 1,
        "name": "CI",
        "prettyref": "main",
        "commit_sha": "abc123def456",
        "status": "failure",
        "repository": {
            "full_name": "testadmin/test-failing-ci",
            "clone_url": "http://localhost:3000/testadmin/test-failing-ci.git",
            "default_branch": "main",
            "html_url": "http://localhost:3000/testadmin/test-failing-ci",
        },
        "trigger_user": {"login": "testadmin", "id": 1},
    },
}


class TestParseForgejoActionPayload:
    def test_action_run_failure_payload(self) -> None:
        event = parse_webhook_payload(CIPlatform.FORGEJO, FORGEJO_ACTION_PAYLOAD)
        assert event.platform == CIPlatform.FORGEJO
        assert event.run_id == "22"
        assert event.run_attempt == "1"
        assert event.branch == "main"
        assert event.commit_sha == "abc123def456"
        assert event.repository.full_name == "testadmin/test-failing-ci"
        assert event.status == "failure"
        assert event.author == "testadmin"

    def test_action_payload_extracts_commit_author_from_event_payload(self) -> None:
        # Real Forgejo action_run payloads serialize the raw push event as a
        # JSON string in run.event_payload; commit author is NOT top-level.
        event_payload = {
            "ref": "refs/heads/main",
            "head_commit": {
                "id": "abc123def456",
                "author": {
                    "name": "CI Review Bot",
                    "email": "ci-bot@autofix.internal",
                    "username": "",
                },
            },
        }
        payload = {
            **FORGEJO_ACTION_PAYLOAD,
            "run": {
                **FORGEJO_ACTION_PAYLOAD["run"],
                "event_payload": json.dumps(event_payload),
            },
        }
        event = parse_webhook_payload(CIPlatform.FORGEJO, payload)
        assert event.commit_author == "CI Review Bot"
        assert event.commit_author_email == "ci-bot@autofix.internal"
        # The pusher account is not the author — must stay distinct.
        assert event.author == "testadmin"

    def test_action_payload_extracts_human_commit_author(self) -> None:
        event_payload = {
            "ref": "refs/heads/main",
            "head_commit": {
                "id": "abc123def456",
                "author": {
                    "name": "Darshan Parmar",
                    "email": "darshan@example.com",
                    "username": "",
                },
            },
        }
        payload = {
            **FORGEJO_ACTION_PAYLOAD,
            "run": {
                **FORGEJO_ACTION_PAYLOAD["run"],
                "event_payload": json.dumps(event_payload),
            },
        }
        event = parse_webhook_payload(CIPlatform.FORGEJO, payload)
        assert event.commit_author == "Darshan Parmar"
        assert event.commit_author_email == "darshan@example.com"

    def test_action_payload_malformed_event_payload(self) -> None:
        payload = {
            **FORGEJO_ACTION_PAYLOAD,
            "run": {
                **FORGEJO_ACTION_PAYLOAD["run"],
                "event_payload": "{not valid json",
            },
        }
        event = parse_webhook_payload(CIPlatform.FORGEJO, payload)
        assert event.commit_author == ""
        assert event.commit_author_email == ""

    def test_action_payload_without_event_payload(self) -> None:
        event = parse_webhook_payload(CIPlatform.FORGEJO, FORGEJO_ACTION_PAYLOAD)
        assert event.commit_author == ""
        assert event.commit_author_email == ""
