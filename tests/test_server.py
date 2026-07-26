from __future__ import annotations

import json

from fastapi.testclient import TestClient

from server import app


client = TestClient(app)


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
        resp = client.post(
            "/webhook/forgejo",
            content=json.dumps(FORGEJO_PAYLOAD),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 202

    def test_rejects_invalid_json(self) -> None:
        resp = client.post(
            "/webhook/forgejo",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400


class TestGithubWebhook:
    def test_accepts_workflow_run_event(self) -> None:
        resp = client.post(
            "/webhook/github",
            content=json.dumps(GITHUB_PAYLOAD),
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "workflow_run",
            },
        )
        assert resp.status_code == 202

    def test_ignores_non_workflow_run_event(self) -> None:
        resp = client.post(
            "/webhook/github",
            content=json.dumps(GITHUB_PAYLOAD),
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "push",
            },
        )
        assert resp.status_code == 200

    def test_rejects_invalid_json(self) -> None:
        resp = client.post(
            "/webhook/github",
            content="not json",
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "workflow_run",
            },
        )
        assert resp.status_code == 400
