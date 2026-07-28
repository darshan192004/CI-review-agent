from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from config import settings
from server import app
from services.auth import User, create_session


_client = TestClient(app, raise_server_exceptions=False)


def _admin_cookie() -> dict[str, str]:
    token = create_session(User(username=settings.admin_username or "testadmin", role="admin"))
    return {"session_token": token}


def _viewer_cookie() -> dict[str, str]:
    token = create_session(User(username="testviewer", role="viewer"))
    return {"session_token": token}


class TestLoginPage:
    def test_login_page_renders(self) -> None:
        resp = _client.get("/login")
        assert resp.status_code == 200
        assert "Sign in" in resp.text

    def test_login_invalid_credentials(self) -> None:
        resp = _client.post("/login", data={"username": "bad", "password": "bad"})
        assert resp.status_code == 200
        assert "Invalid credentials" in resp.text

    def test_login_sets_cookie(self) -> None:
        with patch.object(settings, "admin_username", "admin"), \
             patch.object(settings, "admin_password", "secret123"):
            resp = _client.post(
                "/login", data={"username": "admin", "password": "secret123"},
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert "session_token" in resp.cookies

    def test_logout_clears_cookie(self) -> None:
        cookies = _admin_cookie()
        resp = _client.post("/logout", cookies=cookies, follow_redirects=False)
        assert resp.status_code == 302
        set_cookie_headers = resp.headers.get_list("set-cookie")
        assert any("session_token=" in h for h in set_cookie_headers)


class TestDashboardPage:
    def test_returns_200(self) -> None:
        resp = _client.get("/", cookies=_admin_cookie())
        assert resp.status_code == 200

    def test_returns_html(self) -> None:
        resp = _client.get("/", cookies=_admin_cookie())
        assert "text/html" in resp.headers["content-type"]

    def test_contains_dashboard_heading(self) -> None:
        resp = _client.get("/", cookies=_admin_cookie())
        assert "Dashboard" in resp.text

    def test_contains_metrics(self) -> None:
        resp = _client.get("/", cookies=_admin_cookie())
        assert "metric-active" in resp.text
        assert "metric-success" in resp.text
        assert "metric-failed" in resp.text

    def test_unauthenticated_redirects(self) -> None:
        resp = _client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]


class TestConfigPage:
    def test_returns_200(self) -> None:
        resp = _client.get("/config", cookies=_admin_cookie())
        assert resp.status_code == 200

    def test_contains_config_form(self) -> None:
        resp = _client.get("/config", cookies=_admin_cookie())
        assert "Configuration" in resp.text
        assert "settings-form" in resp.text

    def test_contains_settings_fields(self) -> None:
        resp = _client.get("/config", cookies=_admin_cookie())
        assert "llm_provider" in resp.text
        assert "mcp_server_command" in resp.text
        assert "messaging_platform" in resp.text


class TestRunsPage:
    def test_returns_200(self) -> None:
        resp = _client.get("/runs", cookies=_admin_cookie())
        assert resp.status_code == 200

    def test_contains_runs_heading(self) -> None:
        resp = _client.get("/runs", cookies=_admin_cookie())
        assert "Run History" in resp.text

    def test_filter_by_status(self) -> None:
        resp = _client.get("/runs?status=processing", cookies=_admin_cookie())
        assert resp.status_code == 200

    def test_filter_by_platform(self) -> None:
        resp = _client.get("/runs?platform=github", cookies=_admin_cookie())
        assert resp.status_code == 200


class TestSettingsAPI:
    def test_get_settings_returns_dict(self) -> None:
        resp = _client.get("/api/settings", cookies=_admin_cookie())
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_put_settings_saves(self) -> None:
        with patch("ui.app.write_env") as mock_write:
            resp = _client.put(
                "/api/settings",
                content=json.dumps({"openai_model": "gpt-4o-test"}),
                headers={"Content-Type": "application/json"},
                cookies=_admin_cookie(),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["saved"] == 1
            assert "openai_model" in data["keys"]
            mock_write.assert_called_once()

    def test_put_rejects_invalid_json(self) -> None:
        resp = _client.put(
            "/api/settings",
            content="not json",
            headers={"Content-Type": "application/json"},
            cookies=_admin_cookie(),
        )
        assert resp.status_code == 400

    def test_put_filters_unknown_keys(self) -> None:
        with patch("ui.app.write_env") as mock_write:
            resp = _client.put(
                "/api/settings",
                content=json.dumps({"openai_model": "gpt-4o", "evil_key": "bad"}),
                headers={"Content-Type": "application/json"},
                cookies=_admin_cookie(),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["saved"] == 1
            assert "evil_key" not in data["keys"]

    def test_viewer_cannot_put_settings(self) -> None:
        resp = _client.put(
            "/api/settings",
            content=json.dumps({"openai_model": "gpt-4o-test"}),
            headers={"Content-Type": "application/json"},
            cookies=_viewer_cookie(),
        )
        assert resp.status_code == 403

    def test_unauthenticated_get_settings_redirects(self) -> None:
        resp = _client.get("/api/settings", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]


class TestDashboardPartial:
    def test_returns_html_table(self) -> None:
        resp = _client.get("/api/dashboard/partial", cookies=_admin_cookie())
        assert resp.status_code == 200
        assert "<table" in resp.text
        assert "Repository" in resp.text


class TestConnectionTestEndpoints:
    @patch("ui.app.settings")
    def test_github_no_token(self, mock_settings: object) -> None:
        mock_settings.github_token = ""
        resp = _client.post("/api/test/github", cookies=_admin_cookie())
        assert resp.status_code == 400

    @patch("ui.app.settings")
    def test_forgejo_not_configured(self, mock_settings: object) -> None:
        mock_settings.forgejo_token = ""
        mock_settings.forgejo_base_url = "https://forgejo.example.com"
        resp = _client.post("/api/test/forgejo", cookies=_admin_cookie())
        assert resp.status_code == 400

    @patch("asyncio.create_subprocess_exec", new_callable=AsyncMock)
    def test_mcp_binary_not_found(self, mock_exec: object) -> None:
        mock_exec.side_effect = FileNotFoundError("not found")
        resp = _client.post("/api/test/mcp", cookies=_admin_cookie())
        assert resp.status_code == 502
        data = resp.json()
        assert "Binary not found" in data["detail"]

    @patch("asyncio.create_subprocess_exec", new_callable=AsyncMock)
    def test_messaging_binary_not_found(self, mock_exec: object) -> None:
        mock_exec.side_effect = FileNotFoundError("not found")
        resp = _client.post("/api/test/messaging", cookies=_admin_cookie())
        assert resp.status_code == 502
        data = resp.json()
        assert "Binary not found" in data["detail"]

    def test_viewer_cannot_trigger_test(self) -> None:
        resp = _client.post("/api/test/github", cookies=_viewer_cookie())
        assert resp.status_code == 403


class TestSyncEndpoint:
    def test_sync_requires_admin(self) -> None:
        resp = _client.post("/api/sync-runs", cookies=_viewer_cookie(), json={"repo": "a/b"})
        assert resp.status_code == 403

    def test_sync_requires_auth(self) -> None:
        resp = _client.post("/api/sync-runs", follow_redirects=False)
        assert resp.status_code == 302

    def test_sync_invalid_repo_format(self) -> None:
        resp = _client.post("/api/sync-runs", cookies=_admin_cookie(), json={"repo": "no-slash"})
        assert resp.status_code == 400
        assert "owner/repo" in resp.json()["detail"]

    @patch("ui.app.create_ci_client")
    def test_sync_success(self, mock_create: object) -> None:
        mock_client = AsyncMock()
        mock_client.list_runs = AsyncMock(return_value=[
            {"id": 1, "status": "success", "branch": "main", "commit_sha": "abc123", "actor": {"login": "user1"}},
            {"id": 2, "status": "failure", "branch": "main", "commit_sha": "def456", "actor": {"login": "user2"}},
        ])
        mock_client.close = AsyncMock()
        mock_create.return_value = mock_client

        resp = _client.post("/api/sync-runs", cookies=_admin_cookie(), json={
            "repo": "testadmin/test-failing-ci", "branch": "main", "limit": 10,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["synced"] == 2
        assert data["repo"] == "testadmin/test-failing-ci"


class TestClearHistory:
    def test_clear_requires_admin(self) -> None:
        resp = _client.post("/api/clear-history", cookies=_viewer_cookie())
        assert resp.status_code == 403

    def test_clear_requires_auth(self) -> None:
        resp = _client.post("/api/clear-history", follow_redirects=False)
        assert resp.status_code == 302

    def test_clear_success(self) -> None:
        resp = _client.post("/api/clear-history", cookies=_admin_cookie())
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestSyncPage:
    def test_sync_page_requires_admin(self) -> None:
        resp = _client.get("/sync", cookies=_viewer_cookie(), follow_redirects=False)
        assert resp.status_code == 403

    def test_sync_page_renders(self) -> None:
        resp = _client.get("/sync", cookies=_admin_cookie())
        assert resp.status_code == 200
        assert "Sync from Forgejo" in resp.text
