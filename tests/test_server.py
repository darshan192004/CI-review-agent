from __future__ import annotations

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from server import app


client = TestClient(app)

WEBHOOK_SECRET = "ci-agent-webhook-secret-2026"


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
