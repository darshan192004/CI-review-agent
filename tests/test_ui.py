from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from server import app


client = TestClient(app)


class TestDashboardPage:
    def test_returns_200(self) -> None:
        resp = client.get("/")
        assert resp.status_code == 200

    def test_returns_html(self) -> None:
        resp = client.get("/")
        assert "text/html" in resp.headers["content-type"]

    def test_contains_dashboard_heading(self) -> None:
        resp = client.get("/")
        assert "Dashboard" in resp.text

    def test_contains_metrics(self) -> None:
        resp = client.get("/")
        assert "metric-active" in resp.text
        assert "metric-success" in resp.text
        assert "metric-failed" in resp.text


class TestConfigPage:
    def test_returns_200(self) -> None:
        resp = client.get("/config")
        assert resp.status_code == 200

    def test_contains_config_form(self) -> None:
        resp = client.get("/config")
        assert "Configuration" in resp.text
        assert "settings-form" in resp.text

    def test_contains_settings_fields(self) -> None:
        resp = client.get("/config")
        assert "llm_provider" in resp.text
        assert "mcp_server_command" in resp.text
        assert "messaging_platform" in resp.text


class TestRunsPage:
    def test_returns_200(self) -> None:
        resp = client.get("/runs")
        assert resp.status_code == 200

    def test_contains_runs_heading(self) -> None:
        resp = client.get("/runs")
        assert "Run History" in resp.text

    def test_filter_by_status(self) -> None:
        resp = client.get("/runs?status=processing")
        assert resp.status_code == 200

    def test_filter_by_platform(self) -> None:
        resp = client.get("/runs?platform=github")
        assert resp.status_code == 200


class TestSettingsAPI:
    def test_get_settings_returns_dict(self) -> None:
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_put_settings_saves(self) -> None:
        with patch("ui.app.write_env") as mock_write:
            resp = client.put(
                "/api/settings",
                content=json.dumps({"openai_model": "gpt-4o-test"}),
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["saved"] == 1
            assert "openai_model" in data["keys"]
            mock_write.assert_called_once()

    def test_put_rejects_invalid_json(self) -> None:
        resp = client.put(
            "/api/settings",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    def test_put_filters_unknown_keys(self) -> None:
        with patch("ui.app.write_env") as mock_write:
            resp = client.put(
                "/api/settings",
                content=json.dumps({"openai_model": "gpt-4o", "evil_key": "bad"}),
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["saved"] == 1
            assert "evil_key" not in data["keys"]


class TestDashboardPartial:
    def test_returns_html_table(self) -> None:
        resp = client.get("/api/dashboard/partial")
        assert resp.status_code == 200
        assert "<table" in resp.text
        assert "Repository" in resp.text


class TestConnectionTestEndpoints:
    @patch("ui.app.settings")
    def test_github_no_token(self, mock_settings: object) -> None:
        import types

        mock_settings.github_token = ""
        resp = client.post("/api/test/github")
        assert resp.status_code == 400

    @patch("ui.app.settings")
    def test_forgejo_not_configured(self, mock_settings: object) -> None:
        import types

        mock_settings.forgejo_token = ""
        mock_settings.forgejo_base_url = "https://forgejo.example.com"
        resp = client.post("/api/test/forgejo")
        assert resp.status_code == 400

    @patch("asyncio.create_subprocess_exec", new_callable=AsyncMock)
    def test_mcp_binary_not_found(self, mock_exec: object) -> None:
        mock_exec.side_effect = FileNotFoundError("not found")
        resp = client.post("/api/test/mcp")
        assert resp.status_code == 502
        data = resp.json()
        assert "Binary not found" in data["detail"]

    @patch("asyncio.create_subprocess_exec", new_callable=AsyncMock)
    def test_messaging_binary_not_found(self, mock_exec: object) -> None:
        mock_exec.side_effect = FileNotFoundError("not found")
        resp = client.post("/api/test/messaging")
        assert resp.status_code == 502
        data = resp.json()
        assert "Binary not found" in data["detail"]
