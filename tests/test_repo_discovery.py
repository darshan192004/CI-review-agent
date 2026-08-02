from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from config import settings
from services.repo_discovery import (
    clear_discovery_cache,
    discover_repos,
    fetch_repo_run_history,
    resolve_scope,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_discovery_cache()


class TestResolveScope:
    def test_auto_default_uses_token_owner(self) -> None:
        with (
            patch.object(settings, "github_discovery_mode", "auto"),
            patch.object(settings, "github_org", ""),
            patch.object(settings, "github_username", ""),
            patch.object(settings, "discovery_configured", "false"),
        ):
            assert resolve_scope("github") == ("auto", "")

    def test_user_mode_uses_username(self) -> None:
        with (
            patch.object(settings, "github_discovery_mode", "user"),
            patch.object(settings, "github_username", "octocat"),
            patch.object(settings, "discovery_configured", "true"),
        ):
            assert resolve_scope("github") == ("user", "octocat")

    def test_user_mode_without_username_falls_back_to_auto(self) -> None:
        with (
            patch.object(settings, "github_discovery_mode", "user"),
            patch.object(settings, "github_username", ""),
            patch.object(settings, "discovery_configured", "true"),
        ):
            assert resolve_scope("github") == ("auto", "")

    def test_org_mode_uses_org_setting(self) -> None:
        with (
            patch.object(settings, "forgejo_discovery_mode", "org"),
            patch.object(settings, "forgejo_org", "acme"),
            patch.object(settings, "discovery_configured", "true"),
        ):
            assert resolve_scope("forgejo") == ("org", "acme")

    def test_org_mode_without_org_falls_back_to_auto(self) -> None:
        with (
            patch.object(settings, "forgejo_discovery_mode", "org"),
            patch.object(settings, "forgejo_org", ""),
            patch.object(settings, "discovery_configured", "true"),
        ):
            assert resolve_scope("forgejo") == ("auto", "")

    def test_auto_keeps_legacy_org_scope_until_explicit_config(self) -> None:
        # Before discovery modes existed, an org was the only scope knob.
        # Preserve that behavior until the admin explicitly configures.
        with (
            patch.object(settings, "forgejo_discovery_mode", "auto"),
            patch.object(settings, "forgejo_org", "legacyco"),
            patch.object(settings, "forgejo_username", ""),
            patch.object(settings, "discovery_configured", "false"),
        ):
            assert resolve_scope("forgejo") == ("org", "legacyco")

    def test_auto_ignores_org_after_explicit_config(self) -> None:
        with (
            patch.object(settings, "forgejo_discovery_mode", "auto"),
            patch.object(settings, "forgejo_org", "legacyco"),
            patch.object(settings, "forgejo_username", ""),
            patch.object(settings, "discovery_configured", "true"),
        ):
            assert resolve_scope("forgejo") == ("auto", "")


class TestDiscoverRepos:
    @pytest.mark.asyncio
    async def test_ok_returns_structured_result(self) -> None:
        class _FakeClient:
            async def list_repos(self, scope: str, value: str = "") -> list[str]:
                return ["a/repo-x", "a/repo-y"]

            async def close(self) -> None:
                return None

        with (
            patch.object(settings, "forgejo_token", "tok"),
            patch.object(settings, "forgejo_discovery_mode", "auto"),
            patch.object(settings, "forgejo_org", ""),
            patch.object(settings, "forgejo_username", ""),
            patch.object(settings, "discovery_configured", "false"),
            patch("services.repo_discovery.create_ci_client", return_value=_FakeClient()),
        ):
            result = await discover_repos("forgejo", force=True)

        assert result["status"] == "ok"
        assert result["repos"] == ["a/repo-x", "a/repo-y"]
        assert result["configured"] is False
        assert "repo" in result["detail"].lower()

    @pytest.mark.asyncio
    async def test_not_configured_without_token(self) -> None:
        with (
            patch.object(settings, "forgejo_token", ""),
            patch.object(settings, "discovery_configured", "false"),
        ):
            result = await discover_repos("forgejo", force=True)

        assert result["status"] == "not_configured"
        assert result["repos"] == []

    @pytest.mark.asyncio
    async def test_error_on_api_failure(self) -> None:
        class _BoomClient:
            async def list_repos(self, scope: str, value: str = "") -> list[str]:
                raise RuntimeError("connection refused")

            async def close(self) -> None:
                return None

        with (
            patch.object(settings, "forgejo_token", "tok"),
            patch.object(settings, "forgejo_discovery_mode", "auto"),
            patch.object(settings, "forgejo_org", ""),
            patch.object(settings, "forgejo_username", ""),
            patch.object(settings, "discovery_configured", "true"),
            patch("services.repo_discovery.create_ci_client", return_value=_BoomClient()),
        ):
            result = await discover_repos("forgejo", force=True)

        assert result["status"] == "error"
        assert result["repos"] == []
        assert result["configured"] is True
        assert "connection refused" in result["detail"]


class TestFetchRunHistory:
    @pytest.mark.asyncio
    async def test_passes_ci_created_at_to_record(self) -> None:
        class _FakeClient:
            async def list_repos(self, scope: str, value: str = "") -> list[str]:
                return ["a/repo-x"]

            async def list_runs(self, owner: str, repo: str, branch: str, limit: int = 1) -> list[dict]:
                return [
                    {
                        "id": "101",
                        "status": "completed",
                        "conclusion": "success",
                        "run_attempt": 1,
                        "head_branch": "main",
                        "head_sha": "abc123",
                        "created_at": "2024-05-01T10:30:00Z",
                    }
                ]

            async def close(self) -> None:
                return None

        mock_tracker = AsyncMock()
        mock_tracker.record = AsyncMock()
        with (
            patch.object(settings, "forgejo_token", "tok"),
            patch.object(settings, "forgejo_base_url", "http://localhost:3000"),
            patch.object(settings, "forgejo_discovery_mode", "auto"),
            patch.object(settings, "forgejo_org", ""),
            patch.object(settings, "forgejo_username", ""),
            patch.object(settings, "discovery_configured", "false"),
            patch.object(settings, "github_token", ""),
            patch("services.repo_discovery.create_ci_client", return_value=_FakeClient()),
            patch("services.repo_discovery.run_tracker", mock_tracker),
        ):
            total = await fetch_repo_run_history()

        assert total == 1
        mock_tracker.record.assert_called_once()
        # The CI API's run created_at is passed through so "Run Time" reflects
        # when the run actually started, not when history was seeded.
        assert mock_tracker.record.call_args.kwargs["created_at"] == pytest.approx(1714559400.0)

    @pytest.mark.asyncio
    async def test_synthesizes_descending_created_at_when_provider_omits_it(self) -> None:
        class _FakeClient:
            async def list_repos(self, scope: str, value: str = "") -> list[str]:
                return ["a/repo-x"]

            async def list_runs(self, owner: str, repo: str, branch: str, limit: int = 1) -> list[dict]:
                return [
                    {"id": "102", "status": "completed", "conclusion": "success"},
                    {"id": "101", "status": "completed", "conclusion": "success"},
                ]

            async def close(self) -> None:
                return None

        mock_tracker = AsyncMock()
        mock_tracker.record = AsyncMock()
        with (
            patch.object(settings, "forgejo_token", "tok"),
            patch.object(settings, "forgejo_base_url", "http://localhost:3000"),
            patch.object(settings, "forgejo_discovery_mode", "auto"),
            patch.object(settings, "forgejo_org", ""),
            patch.object(settings, "forgejo_username", ""),
            patch.object(settings, "discovery_configured", "false"),
            patch.object(settings, "github_token", ""),
            patch("services.repo_discovery.create_ci_client", return_value=_FakeClient()),
            patch("services.repo_discovery.run_tracker", mock_tracker),
            patch("services.repo_discovery.time.time", return_value=1_700_000_000.0),
        ):
            total = await fetch_repo_run_history()

        assert total == 2
        calls = mock_tracker.record.call_args_list
        # Newest run (first in the provider's list) must anchor to the later
        # timestamp so ORDER BY created_at DESC keeps the provider's order.
        assert calls[0].kwargs["created_at"] == pytest.approx(1_700_000_000.0)
        assert calls[1].kwargs["created_at"] == pytest.approx(1_699_999_999.0)
        assert all(call.kwargs["force_created_at"] is True for call in calls)
