from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

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


@pytest.fixture
def _seeded_runs_db(tmp_path):
    import services.run_tracker as rt

    original = rt._DB_PATH
    rt._DB_PATH = tmp_path / "ci_runs.db"
    yield
    rt._DB_PATH = original


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
        with patch.object(settings, "admin_username", "admin"), patch.object(settings, "admin_password", "secret123"):
            resp = _client.post(
                "/login",
                data={"username": "admin", "password": "secret123"},
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
        assert "stat-processing-jobs" in resp.text
        assert "stat-succeeded-runs" in resp.text
        assert "stat-failed-runs" in resp.text

    def test_contains_repo_empty_state(self) -> None:
        resp = _client.get("/", cookies=_admin_cookie())
        assert "repo-empty-state" in resp.text
        assert "/config" in resp.text

    def test_contains_history_skeleton(self) -> None:
        resp = _client.get("/", cookies=_admin_cookie())
        assert "runs-skeleton" in resp.text
        assert 'hx-indicator="#runs-skeleton"' in resp.text

    def test_discovery_popup_has_a11y_attributes(self) -> None:
        resp = _client.get("/", cookies=_admin_cookie())
        assert 'role="dialog"' in resp.text
        assert 'aria-modal="true"' in resp.text
        assert 'aria-labelledby="discovery-popup-title"' in resp.text

    def test_repo_status_announces_live(self) -> None:
        resp = _client.get("/", cookies=_admin_cookie())
        assert 'aria-live="polite"' in resp.text

    @pytest.mark.asyncio
    async def test_table_headers_have_scope(self, _seeded_runs_db) -> None:
        from services.run_tracker import run_tracker

        await run_tracker.record("owner/alpha", "1", status="PASSED", platform="github")

        resp = _client.get("/", cookies=_admin_cookie())
        assert 'scope="col"' in resp.text
        assert "overflow-x-auto" in resp.text

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

    def test_contains_discovery_fields(self) -> None:
        resp = _client.get("/config", cookies=_admin_cookie())
        assert "forgejo_discovery_mode" in resp.text
        assert "github_discovery_mode" in resp.text
        assert "forgejo_org" in resp.text
        assert "github_org" in resp.text
        assert "forgejo_username" in resp.text
        assert "github_username" in resp.text

    def test_contains_rate_limit_fields(self) -> None:
        resp = _client.get("/config", cookies=_admin_cookie())
        assert "llm_rate_limit_per_minute" in resp.text
        assert "llm_rate_limit_burst" in resp.text
        assert "llm_max_retries" in resp.text
        assert "llm_retry_backoff_seconds" in resp.text

    def test_contains_server_fields_with_restart_hint(self) -> None:
        resp = _client.get("/config", cookies=_admin_cookie())
        assert "server_host" in resp.text
        assert "server_port" in resp.text
        assert "restart" in resp.text.lower()

    def test_contains_webhook_secrets_and_remaining_keys(self) -> None:
        resp = _client.get("/config", cookies=_admin_cookie())
        assert "forgejo_webhook_secret" in resp.text
        assert "github_webhook_secret" in resp.text
        assert "forgejo_base_url" in resp.text
        assert "auto_fix_reruns" in resp.text

    def test_theme_toggle_has_aria_label(self) -> None:
        resp = _client.get("/config", cookies=_admin_cookie())
        assert 'aria-label="Toggle color theme"' in resp.text
        assert "aria-pressed" in resp.text


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

    def test_contains_filter_aria_labels(self) -> None:
        resp = _client.get("/runs", cookies=_admin_cookie())
        assert 'aria-label="Search runs"' in resp.text
        assert 'aria-label="Filter by status"' in resp.text
        assert 'aria-label="Filter by platform"' in resp.text

    def test_contains_filter_loading_indicator(self) -> None:
        resp = _client.get("/runs", cookies=_admin_cookie())
        assert "runs-filter-spinner" in resp.text
        assert "htmx-indicator" in resp.text
        assert 'hx-indicator="#runs-filter-spinner"' in resp.text

    def test_runs_container_allows_horizontal_scroll(self) -> None:
        resp = _client.get("/runs", cookies=_admin_cookie())
        assert 'id="runs-container"' in resp.text
        assert "overflow-x-auto" in resp.text

    @pytest.mark.asyncio
    async def test_table_headers_have_scope(self, _seeded_runs_db) -> None:
        from services.run_tracker import run_tracker

        await run_tracker.record("owner/alpha", "1", status="PASSED", platform="github")

        resp = _client.get("/runs", cookies=_admin_cookie())
        assert 'scope="col"' in resp.text


class TestRunsPageHxFragment:
    @pytest.fixture
    def _isolated_runs_db(self, tmp_path):
        import services.run_tracker as rt

        original = rt._DB_PATH
        rt._DB_PATH = tmp_path / "ci_runs.db"
        yield
        rt._DB_PATH = original

    @pytest.mark.asyncio
    async def test_hx_request_returns_fragment_not_full_page(self, _isolated_runs_db) -> None:
        from services.run_tracker import run_tracker

        await run_tracker.record("owner/alpha", "1", status="PASSED", platform="github")

        resp = _client.get("/runs", headers={"HX-Request": "true"}, cookies=_admin_cookie())
        assert resp.status_code == 200
        assert "<!DOCTYPE html>" not in resp.text
        assert "<nav" not in resp.text
        assert 'id="runs-table-body"' in resp.text

    @pytest.mark.asyncio
    async def test_non_hx_request_returns_full_page(self, _isolated_runs_db) -> None:
        from services.run_tracker import run_tracker

        await run_tracker.record("owner/alpha", "1", status="PASSED", platform="github")

        resp = _client.get("/runs", cookies=_admin_cookie())
        assert resp.status_code == 200
        assert "<!DOCTYPE html>" in resp.text
        assert "<nav" in resp.text

    @pytest.mark.asyncio
    async def test_hx_request_filters_by_status(self, _isolated_runs_db) -> None:
        from services.run_tracker import run_tracker

        await run_tracker.record("owner/alpha", "1", status="PASSED", platform="github")
        await run_tracker.record("owner/beta", "2", status="FAILED", platform="forgejo")

        resp = _client.get(
            "/runs?status=PASSED",
            headers={"HX-Request": "true"},
            cookies=_admin_cookie(),
        )
        assert resp.status_code == 200
        assert "<!DOCTYPE html>" not in resp.text
        assert "owner/alpha" in resp.text
        assert "owner/beta" not in resp.text

    @pytest.mark.asyncio
    async def test_hx_request_filters_by_platform(self, _isolated_runs_db) -> None:
        from services.run_tracker import run_tracker

        await run_tracker.record("owner/alpha", "1", status="PASSED", platform="github")
        await run_tracker.record("owner/beta", "2", status="FAILED", platform="forgejo")

        resp = _client.get(
            "/runs?platform=forgejo",
            headers={"HX-Request": "true"},
            cookies=_admin_cookie(),
        )
        assert resp.status_code == 200
        assert "<!DOCTYPE html>" not in resp.text
        assert "owner/beta" in resp.text
        assert "owner/alpha" not in resp.text

    @pytest.mark.asyncio
    async def test_hx_fragment_wraps_table_for_scroll(self, _isolated_runs_db) -> None:
        from services.run_tracker import run_tracker

        await run_tracker.record("owner/alpha", "1", status="PASSED", platform="github")

        resp = _client.get("/runs", headers={"HX-Request": "true"}, cookies=_admin_cookie())
        assert resp.status_code == 200
        assert 'class="overflow-x-auto"' in resp.text
        assert 'scope="col"' in resp.text


class TestA11yCss:
    def test_stylesheet_served(self) -> None:
        resp = _client.get("/static/css/styles.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]

    def test_reduced_motion_supported(self) -> None:
        resp = _client.get("/static/css/styles.css")
        assert "prefers-reduced-motion" in resp.text

    def test_focus_visible_rules_present(self) -> None:
        resp = _client.get("/static/css/styles.css")
        assert ":focus-visible" in resp.text

    def test_skeleton_styles_present(self) -> None:
        resp = _client.get("/static/css/styles.css")
        assert ".skeleton" in resp.text
        assert "shimmer" in resp.text

    def test_dim_text_dark_meets_contrast(self) -> None:
        resp = _client.get("/static/css/styles.css")
        assert "--text-dim: #8b93a5" in resp.text

    def test_dim_text_light_meets_contrast(self) -> None:
        resp = _client.get("/static/css/styles.css")
        assert "--text-dim: #64748b" in resp.text


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

    def test_put_rate_limits_persist_and_coerce(self) -> None:
        payload = {
            "llm_rate_limit_per_minute": "30",
            "llm_rate_limit_burst": "10",
            "llm_max_retries": "5",
            "llm_retry_backoff_seconds": "1.5",
        }
        with patch("ui.app.write_env") as mock_write:
            resp = _client.put(
                "/api/settings",
                content=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                cookies=_admin_cookie(),
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["saved"] == 4
        for key in payload:
            assert key in data["keys"]
        mock_write.assert_called_once()
        from config import settings as live

        assert live.llm_rate_limit_per_minute == 30
        assert live.llm_rate_limit_burst == 10
        assert live.llm_max_retries == 5
        assert live.llm_retry_backoff_seconds == 1.5

    def test_put_empty_rate_limit_uses_disabled_default(self) -> None:
        with patch("ui.app.write_env") as mock_write:
            resp = _client.put(
                "/api/settings",
                content=json.dumps({"llm_rate_limit_per_minute": ""}),
                headers={"Content-Type": "application/json"},
                cookies=_admin_cookie(),
            )
        assert resp.status_code == 200
        assert resp.json()["saved"] == 1
        written = mock_write.call_args.args[0]
        assert written["llm_rate_limit_per_minute"] == "0"

    def test_put_webhook_secrets_persist(self) -> None:
        payload = {"forgejo_webhook_secret": "fj-secret", "github_webhook_secret": "gh-secret"}
        with patch("ui.app.write_env") as mock_write:
            resp = _client.put(
                "/api/settings",
                content=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                cookies=_admin_cookie(),
            )
        assert resp.status_code == 200
        assert resp.json()["saved"] == 2
        written = mock_write.call_args.args[0]
        assert written["forgejo_webhook_secret"] == "fj-secret"
        assert written["github_webhook_secret"] == "gh-secret"

    def test_put_form_encoded_discovery_key(self) -> None:
        with patch("ui.app.write_env") as mock_write:
            resp = _client.put("/api/settings", data={"forgejo_org": "acme"}, cookies=_admin_cookie())
        assert resp.status_code == 200
        data = resp.json()
        assert "forgejo_org" in data["keys"]
        written = mock_write.call_args.args[0]
        assert written["forgejo_org"] == "acme"

    def test_put_ignores_redacted_secrets(self) -> None:
        with patch("ui.app.write_env") as mock_write:
            resp = _client.put(
                "/api/settings",
                content=json.dumps({"github_token": "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022"}),
                headers={"Content-Type": "application/json"},
                cookies=_admin_cookie(),
            )
        assert resp.status_code == 200
        assert resp.json()["saved"] == 0
        mock_write.assert_not_called()


class TestDashboardPartial:
    def test_returns_html_table(self) -> None:
        resp = _client.get("/api/dashboard/partial", cookies=_admin_cookie())
        assert resp.status_code == 200
        assert "<table" in resp.text
        assert "Repository" in resp.text

    def test_no_oob_metrics_leak(self) -> None:
        resp = _client.get("/api/dashboard/partial", cookies=_admin_cookie())
        assert "stat-processing-jobs" not in resp.text
        assert "hx-swap-oob" not in resp.text


class TestMetricsPartial:
    def test_returns_metric_values(self) -> None:
        resp = _client.get("/api/metrics/partial", cookies=_admin_cookie())
        assert resp.status_code == 200
        assert "stat-processing-jobs" in resp.text
        assert "stat-succeeded-runs" in resp.text
        assert "stat-failed-runs" in resp.text
        assert "stat-system-uptime" in resp.text

    def test_no_table_leak(self) -> None:
        resp = _client.get("/api/metrics/partial", cookies=_admin_cookie())
        assert "<table" not in resp.text


class TestConnectionTestEndpoints:
    @patch("ui.app.settings")
    def test_github_no_token(self, mock_settings: object) -> None:
        mock_settings.github_token = ""
        resp = _client.post("/api/test/github", cookies=_admin_cookie())
        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False
        assert data["detail"] == "GitHub token not configured"

    @patch("ui.app.settings")
    def test_forgejo_not_configured(self, mock_settings: object) -> None:
        mock_settings.forgejo_token = ""
        mock_settings.forgejo_base_url = "https://forgejo.example.com"
        resp = _client.post("/api/test/forgejo", cookies=_admin_cookie())
        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False
        assert "token or base URL" in data["detail"]

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


class TestReposAPI:
    @pytest.fixture
    def _isolated_runs_db(self, tmp_path):
        import services.run_tracker as rt

        original = rt._DB_PATH
        rt._DB_PATH = tmp_path / "ci_runs.db"
        yield
        rt._DB_PATH = original

    @pytest.mark.asyncio
    async def test_merges_discovery_and_known_repos(self, _isolated_runs_db) -> None:
        from services.run_tracker import run_tracker

        await run_tracker.record("owner/known", "7", status="FAILED", platform="github")

        with (
            patch("services.repo_discovery.discover_repos", new_callable=AsyncMock) as mock,
            patch("services.repo_discovery.is_discovery_configured", return_value=False),
        ):
            mock.side_effect = [
                {
                    "status": "not_configured",
                    "repos": [],
                    "detail": "No Forgejo token configured.",
                    "configured": False,
                },
                {
                    "status": "ok",
                    "repos": ["owner/repo-a", "owner/repo-b"],
                    "detail": "Discovered 2 repos.",
                    "configured": False,
                },
            ]
            resp = _client.get("/api/repos", cookies=_admin_cookie())

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["configured"] is False
        names = [r["name"] for r in data["repos"]]
        assert "owner/repo-a" in names
        assert "owner/repo-b" in names
        assert "owner/known" in names
        assert mock.await_count == 2

    @pytest.mark.asyncio
    async def test_reports_not_configured_when_no_token(self, _isolated_runs_db) -> None:
        with (
            patch("services.repo_discovery.discover_repos", new_callable=AsyncMock) as mock,
            patch("services.repo_discovery.is_discovery_configured", return_value=False),
        ):
            mock.side_effect = [
                {
                    "status": "not_configured",
                    "repos": [],
                    "detail": "No Forgejo token configured.",
                    "configured": False,
                },
                {"status": "not_configured", "repos": [], "detail": "No GitHub token configured.", "configured": False},
            ]
            resp = _client.get("/api/repos", cookies=_admin_cookie())

        data = resp.json()
        assert data["status"] == "not_configured"
        assert data["repos"] == []
        assert "token" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_reports_error_status(self, _isolated_runs_db) -> None:
        with (
            patch("services.repo_discovery.discover_repos", new_callable=AsyncMock) as mock,
            patch("services.repo_discovery.is_discovery_configured", return_value=True),
        ):
            mock.side_effect = [
                {"status": "error", "repos": [], "detail": "Discovery failed: boom", "configured": True},
                {"status": "error", "repos": [], "detail": "Discovery failed: boom2", "configured": True},
            ]
            resp = _client.get("/api/repos", cookies=_admin_cookie())

        data = resp.json()
        assert data["status"] == "error"
        assert "boom" in data["detail"]


class TestDiscoveryDismiss:
    def test_dismiss_sets_configured_flag(self) -> None:
        with patch("ui.app.write_env") as mock_write:
            resp = _client.post("/api/discovery/dismiss", cookies=_admin_cookie())
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        mock_write.assert_called_once_with({"discovery_configured": "true"})

    def test_dismiss_requires_admin(self) -> None:
        resp = _client.post("/api/discovery/dismiss", cookies=_viewer_cookie())
        assert resp.status_code == 403


class TestWebhookHealthEndpoint:
    @pytest.fixture
    def _isolated_runs_db(self, tmp_path):
        import services.run_tracker as rt

        original = rt._DB_PATH
        rt._DB_PATH = tmp_path / "ci_runs.db"
        yield
        rt._DB_PATH = original

    def test_requires_auth(self) -> None:
        resp = _client.get("/api/webhook-health", follow_redirects=False)
        assert resp.status_code == 302

    @pytest.mark.asyncio
    async def test_empty_when_nothing_configured(self, _isolated_runs_db) -> None:
        from services.run_tracker import run_tracker

        await run_tracker.clear()

        with patch("services.repo_discovery.discover_repos", new_callable=AsyncMock) as mock:
            mock.side_effect = [
                {
                    "status": "not_configured",
                    "repos": [],
                    "detail": "No Forgejo token configured.",
                    "configured": False,
                },
                {"status": "not_configured", "repos": [], "detail": "No GitHub token configured.", "configured": False},
            ]
            resp = _client.get("/api/webhook-health", cookies=_admin_cookie())

        assert resp.status_code == 200
        assert resp.json() == {}

    @pytest.mark.asyncio
    async def test_discovered_only_repos_are_stale(self, _isolated_runs_db) -> None:
        from services.run_tracker import run_tracker

        await run_tracker.clear()
        await run_tracker.record("owner/active", "1", status="PASSED", platform="forgejo")

        with patch("services.repo_discovery.discover_repos", new_callable=AsyncMock) as mock:
            mock.side_effect = [
                {
                    "status": "ok",
                    "repos": ["owner/silent", "owner/active"],
                    "detail": "Discovered 2 repos.",
                    "configured": True,
                },
                {"status": "not_configured", "repos": [], "detail": "No GitHub token configured.", "configured": True},
            ]
            resp = _client.get("/api/webhook-health", cookies=_admin_cookie())

        data = resp.json()
        assert data == {
            "owner/silent": {"status": "stale"},
            "owner/active": {"status": "healthy"},
        }

    @pytest.mark.asyncio
    async def test_merges_both_platforms(self, _isolated_runs_db) -> None:
        from services.run_tracker import run_tracker

        await run_tracker.clear()

        with patch("services.repo_discovery.discover_repos", new_callable=AsyncMock) as mock:
            mock.side_effect = [
                {"status": "ok", "repos": ["fj/one"], "detail": "Discovered 1 repo.", "configured": True},
                {"status": "ok", "repos": ["gh/two"], "detail": "Discovered 1 repo.", "configured": True},
            ]
            resp = _client.get("/api/webhook-health", cookies=_admin_cookie())

        data = resp.json()
        assert data == {
            "fj/one": {"status": "stale"},
            "gh/two": {"status": "stale"},
        }
