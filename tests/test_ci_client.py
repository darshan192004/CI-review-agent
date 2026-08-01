from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from services.ci_client import ForgejoCIClient, GitHubCIClient


class _FakeResponse:
    def __init__(self, payload: list[dict], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("GET", "http://example.com"),
                response=self,
            )

    def json(self) -> list[dict]:
        return self._payload


def _stub_get(client: object) -> AsyncMock:
    get = AsyncMock(return_value=_FakeResponse([{"full_name": "a/b"}]))
    client._client.get = get  # type: ignore[attr-defined]
    return get


@pytest.mark.asyncio
async def test_github_auto_lists_authenticated_user_repos() -> None:
    client = GitHubCIClient(token="t", base_url="https://api.github.com")
    get = _stub_get(client)
    repos = await client.list_repos("auto", "")
    assert repos == ["a/b"]
    assert get.await_args.args[0] == "https://api.github.com/user/repos"


@pytest.mark.asyncio
async def test_github_org_lists_org_repos() -> None:
    client = GitHubCIClient(token="t", base_url="https://api.github.com")
    get = _stub_get(client)
    repos = await client.list_repos("org", "acme")
    assert repos == ["a/b"]
    assert get.await_args.args[0] == "https://api.github.com/orgs/acme/repos"


@pytest.mark.asyncio
async def test_github_user_lists_user_repos() -> None:
    client = GitHubCIClient(token="t", base_url="https://api.github.com")
    get = _stub_get(client)
    await client.list_repos("user", "octocat")
    assert get.await_args.args[0] == "https://api.github.com/users/octocat/repos"


@pytest.mark.asyncio
async def test_forgejo_auto_lists_authenticated_user_repos() -> None:
    client = ForgejoCIClient(token="t", base_url="https://gitea.example.com")
    get = _stub_get(client)
    await client.list_repos("auto", "")
    assert get.await_args.args[0] == "https://gitea.example.com/api/v1/user/repos"


@pytest.mark.asyncio
async def test_forgejo_org_lists_org_repos() -> None:
    client = ForgejoCIClient(token="t", base_url="https://gitea.example.com")
    get = _stub_get(client)
    await client.list_repos("org", "acme")
    assert get.await_args.args[0] == "https://gitea.example.com/api/v1/orgs/acme/repos"


@pytest.mark.asyncio
async def test_forgejo_user_lists_user_repos() -> None:
    client = ForgejoCIClient(token="t", base_url="https://gitea.example.com")
    get = _stub_get(client)
    await client.list_repos("user", "octocat")
    assert get.await_args.args[0] == "https://gitea.example.com/api/v1/users/octocat/repos"
