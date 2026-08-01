from __future__ import annotations

from unittest.mock import patch

import pytest

from config import settings
from services.repo_discovery import clear_discovery_cache, discover_repos, resolve_scope


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
