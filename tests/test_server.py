from __future__ import annotations

import hashlib
import hmac
import json
import os

import pytest
from fastapi.testclient import TestClient

WEBHOOK_SECRET = "ci-agent-webhook-secret-2026"

# Set env vars before importing server
os.environ["GITHUB_WEBHOOK_SECRET"] = WEBHOOK_SECRET
os.environ["FORGEJO_WEBHOOK_SECRET"] = WEBHOOK_SECRET
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "testpass")

# Reload settings to pick up the env vars
from config import settings

_settings_dict = settings.model_dump()
_settings_dict["github_webhook_secret"] = WEBHOOK_SECRET
_settings_dict["forgejo_webhook_secret"] = WEBHOOK_SECRET

# Update the singleton in-place
for k, v in _settings_dict.items():
    object.__setattr__(settings, k, v)

from server import _format_uptime_hms, _resolve_stored_run, app

client = TestClient(app)


def _forgejo_signature(payload: dict) -> str:
    body = json.dumps(payload).encode()
    return hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


FORGEJO_PAYLOAD = {
    "action": "completed",
    "workflow": {"id": 12345, "name": "CI", "head_sha": "abc123"},
    "ref_name": "main",
    "repository": {"full_name": "myorg/myrepo", "default_branch": "main"},
    "sender": {"login": "testuser"},
}

GITHUB_PAYLOAD = {
    "action": "completed",
    "workflow_run": {
        "id": 67890,
        "head_branch": "main",
        "head_sha": "def456",
        "status": "completed",
        "conclusion": "failure",
    },
    "repository": {"full_name": "octocat/Hello-World", "default_branch": "main"},
    "sender": {"login": "octocat"},
}


class TestHealthEndpoint:
    def test_health_returns_ok(self) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    def test_status_returns_ok(self) -> None:
        resp = client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert "uptime_seconds" in data
        assert "active_runs" in data


class TestForgejoWebhook:
    def test_accepts_valid_payload(self) -> None:
        body = json.dumps(FORGEJO_PAYLOAD).encode()
        sig = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        resp = client.post(
            "/webhook/forgejo",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Forgejo-Event": "workflow_run",
                "X-Forgejo-Signature": sig,
            },
        )
        assert resp.status_code == 202

    def test_rejects_invalid_json(self) -> None:
        body = b"not json"
        sig = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        resp = client.post(
            "/webhook/forgejo",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Forgejo-Event": "workflow_run",
                "X-Forgejo-Signature": sig,
            },
        )
        assert resp.status_code == 400


class TestGithubWebhook:
    def test_accepts_workflow_run_event(self) -> None:
        body = json.dumps(GITHUB_PAYLOAD).encode()
        sig = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        resp = client.post(
            "/webhook/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": f"sha256={sig}",
            },
        )
        assert resp.status_code == 202

    def test_ignores_non_workflow_run_event(self) -> None:
        body = json.dumps(GITHUB_PAYLOAD).encode()
        sig = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        resp = client.post(
            "/webhook/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": f"sha256={sig}",
            },
        )
        assert resp.status_code == 200

    def test_rejects_invalid_json(self) -> None:
        body = b"not json"
        sig = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        resp = client.post(
            "/webhook/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": f"sha256={sig}",
            },
        )
        assert resp.status_code == 400


FORGEJO_ACTION_RUN_FAILURE_PAYLOAD = {
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


class TestForgejoActionRunFailure:
    def test_accepts_action_run_failure_payload(self) -> None:
        body = json.dumps(FORGEJO_ACTION_RUN_FAILURE_PAYLOAD).encode()
        sig = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        resp = client.post(
            "/webhook/forgejo",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Forgejo-Event": "action_run_failure",
                "X-Forgejo-Signature": sig,
            },
        )
        assert resp.status_code == 202


class TestFormatUptimeHms:
    def test_zero_seconds(self) -> None:
        assert _format_uptime_hms(0) == "0h 0m 0s"

    def test_seconds_only(self) -> None:
        assert _format_uptime_hms(59) == "0h 0m 59s"

    def test_minutes_and_seconds(self) -> None:
        assert _format_uptime_hms(34 * 60 + 5) == "0h 34m 5s"

    def test_hours_minutes_seconds(self) -> None:
        assert _format_uptime_hms(3600 + 34 * 60 + 5) == "1h 34m 5s"

    def test_ignores_fractional_seconds(self) -> None:
        assert _format_uptime_hms(34 * 60 + 5.9) == "0h 34m 5s"

    def test_clamps_negative(self) -> None:
        assert _format_uptime_hms(-5) == "0h 0m 0s"


class TestResolveStoredRun:
    @pytest.mark.asyncio
    async def test_prefers_get_run_by_session(self, monkeypatch) -> None:
        lifecycle = {"repository": "testorg/testrepo", "run_id": "99", "run_attempt": "1", "created_at": 100.0}

        async def fake_get_run_by_session(repo: str, session_id: int) -> dict | None:
            assert repo == "testorg/testrepo"
            assert session_id == 7
            return lifecycle

        async def fake_get_run(repo: str, run_id: str, run_attempt: str) -> dict | None:
            raise AssertionError("get_run should not be called when session resolves")

        monkeypatch.setattr("server.run_tracker.get_run_by_session", fake_get_run_by_session)
        monkeypatch.setattr("server.run_tracker.get_run", fake_get_run)

        row = await _resolve_stored_run(
            {"repository": "testorg/testrepo", "run_id": "123", "run_attempt": "2", "session_id": 7}
        )
        assert row is lifecycle

    @pytest.mark.asyncio
    async def test_falls_back_to_get_run_when_session_missing(self, monkeypatch) -> None:
        stored = {"repository": "testorg/testrepo", "run_id": "123", "run_attempt": "2", "created_at": 100.0}

        async def fake_get_run(repo: str, run_id: str, run_attempt: str) -> dict | None:
            assert repo == "testorg/testrepo"
            assert run_id == "123"
            assert run_attempt == "2"
            return stored

        monkeypatch.setattr("server.run_tracker.get_run_by_session", lambda *a: None)
        monkeypatch.setattr("server.run_tracker.get_run", fake_get_run)

        row = await _resolve_stored_run({"repository": "testorg/testrepo", "run_id": "123", "run_attempt": "2"})
        assert row is stored

    @pytest.mark.asyncio
    async def test_falls_back_when_session_has_no_row(self, monkeypatch) -> None:
        stored = {"repository": "testorg/testrepo", "run_id": "123", "run_attempt": "2", "created_at": 100.0}

        async def fake_get_run_by_session(repo: str, session_id: int) -> dict | None:
            return None

        async def fake_get_run(repo: str, run_id: str, run_attempt: str) -> dict | None:
            return stored

        monkeypatch.setattr("server.run_tracker.get_run_by_session", fake_get_run_by_session)
        monkeypatch.setattr("server.run_tracker.get_run", fake_get_run)

        row = await _resolve_stored_run(
            {"repository": "testorg/testrepo", "run_id": "123", "run_attempt": "2", "session_id": 7}
        )
        assert row is stored
