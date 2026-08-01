from __future__ import annotations

import abc
import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from services.log_parser import parse_ci_logs

logger = logging.getLogger(__name__)


class CIClientError(Exception):
    pass


class CIClient(abc.ABC):
    def __init__(self, token: str, base_url: str) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=60.0)

    async def close(self) -> None:
        await self._client.aclose()

    @abc.abstractmethod
    async def fetch_logs(self, owner: str, repo: str, run_id: str) -> str: ...

    @abc.abstractmethod
    async def get_run_info(self, owner: str, repo: str, run_id: str) -> dict[str, Any]: ...

    @abc.abstractmethod
    async def list_runs(self, owner: str, repo: str, branch: str, limit: int = 1) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    async def list_repos(self, org: str) -> list[str]: ...

    @abc.abstractmethod
    async def get_commit_author(self, owner: str, repo: str, commit_sha: str) -> tuple[str, str]: ...


class GitHubCIClient(CIClient):
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def fetch_logs(self, owner: str, repo: str, run_id: str) -> str:
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}"
        logger.info("=== GITHUB API: GET run info === url=%s", url)
        resp = await self._client.get(url, headers=self._headers())
        logger.info("=== GITHUB API: run info response === status=%d", resp.status_code)
        resp.raise_for_status()
        run_data = resp.json()
        log_url = run_data.get("jobs_url", "")
        if not log_url:
            raise CIClientError(f"No jobs URL found for run {run_id}")

        logger.info("=== GITHUB API: GET jobs === url=%s", log_url)
        jobs_resp = await self._client.get(log_url, headers=self._headers())
        logger.info("=== GITHUB API: jobs response === status=%d", jobs_resp.status_code)
        jobs_resp.raise_for_status()
        jobs = jobs_resp.json().get("jobs", [])

        all_logs: list[str] = []
        for job in jobs:
            for step in job.get("steps", []):
                name = step.get("name", "unknown")
                conclusion = step.get("conclusion", "")
                if conclusion == "failure":
                    all_logs.append(f"FAILED STEP: {name}")
                    all_logs.append(f"  Status: {step.get('status', 'unknown')}")
                    all_logs.append(f"  Number: {step.get('number', '?')}")

        raw = "\n".join(all_logs) if all_logs else "No failure details found in jobs"
        return parse_ci_logs(raw)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_commit_author(self, owner: str, repo: str, commit_sha: str) -> tuple[str, str]:
        """Get the author name and email for a specific commit."""
        url = f"{self.base_url}/repos/{owner}/{repo}/commits/{commit_sha}"
        logger.info("=== GITHUB API: GET commit === url=%s", url)
        resp = await self._client.get(url, headers=self._headers())
        logger.info("=== GITHUB API: commit response === status=%d", resp.status_code)
        resp.raise_for_status()
        commit_data = resp.json()
        author = commit_data.get("author", {})
        author_name = author.get("name", "") if author else ""
        author_email = author.get("email", "") if author else ""
        logger.info("=== COMMIT AUTHOR === commit=%s author=%s email=%s", commit_sha, author_name, author_email)
        return author_name, author_email

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_run_info(self, owner: str, repo: str, run_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}"
        resp = await self._client.get(url, headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def list_runs(self, owner: str, repo: str, branch: str, limit: int = 1) -> list[dict[str, Any]]:
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs"
        params = {"branch": branch, "per_page": limit}
        resp = await self._client.get(url, headers=self._headers(), params=params)
        resp.raise_for_status()
        return resp.json().get("workflow_runs", [])

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def list_repos(self, org: str) -> list[str]:
        url = f"{self.base_url}/orgs/{org}/repos"
        params = {"per_page": 100, "type": "all"}
        resp = await self._client.get(url, headers=self._headers(), params=params)
        resp.raise_for_status()
        return [r["full_name"] for r in resp.json()]


class ForgejoCIClient(CIClient):
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"token {self.token}",
            "Accept": "application/json",
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_commit_author(self, owner: str, repo: str, commit_sha: str) -> tuple[str, str]:
        """Get the author name and email for a specific commit."""
        url = f"{self.base_url}/api/v1/repos/{owner}/{repo}/git/commits/{commit_sha}"
        logger.info("=== FORGEJO API: GET commit === url=%s", url)
        resp = await self._client.get(url, headers=self._headers())
        logger.info("=== FORGEJO API: commit response === status=%d", resp.status_code)
        resp.raise_for_status()
        commit_data = resp.json()
        # Forgejo's git-commit API nests the author under `commit.author`
        # (top-level `author` is null for non-user commits).
        commit_obj = commit_data.get("commit", {}) or {}
        author = commit_obj.get("author", {}) or {}
        author_name = author.get("name", "")
        author_email = author.get("email", "")
        logger.info("=== COMMIT AUTHOR === commit=%s author=%s email=%s", commit_sha, author_name, author_email)
        return author_name, author_email

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def fetch_logs(self, owner: str, repo: str, run_id: str) -> str:
        # Forgejo/Gitea actions API (run_id is the DB id, matching webhook payloads):
        #   GET /api/v1/repos/{owner}/{repo}/actions/runs/{run_id}
        #   GET /api/v1/repos/{owner}/{repo}/actions/runs/{run_id}/jobs   -> JSON array
        #   GET /api/v1/repos/{owner}/{repo}/actions/jobs/{job_id}/logs   -> plaintext
        run_url = f"{self.base_url}/api/v1/repos/{owner}/{repo}/actions/runs/{run_id}"
        logger.info("=== FORGEJO API: GET run info === url=%s", run_url)
        resp = await self._client.get(run_url, headers=self._headers())
        logger.info("=== FORGEJO API: run info response === status=%d", resp.status_code)
        resp.raise_for_status()

        jobs_url = f"{self.base_url}/api/v1/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
        logger.info("=== FORGEJO API: GET jobs === url=%s", jobs_url)
        jobs_resp = await self._client.get(jobs_url, headers=self._headers())
        logger.info("=== FORGEJO API: jobs response === status=%d", jobs_resp.status_code)
        if jobs_resp.status_code != 200:
            logger.warning(
                "=== FORGEJO API: jobs not available === status=%d url=%s",
                jobs_resp.status_code,
                jobs_url,
            )
            return "No failure details found"
        jobs = jobs_resp.json()
        if not isinstance(jobs, list):
            jobs = (jobs or {}).get("jobs", []) or []

        all_logs: list[str] = []
        for job in jobs:
            job_id = job.get("id")
            if not job_id:
                continue
            log_url = f"{self.base_url}/api/v1/repos/{owner}/{repo}/actions/jobs/{job_id}/logs"
            log_resp = await self._client.get(log_url, headers=self._headers())
            if log_resp.status_code == 200 and log_resp.text:
                all_logs.append(f"--- Job: {job.get('name', job_id)} ---\n{log_resp.text}")
            else:
                job_name = job.get("name", job_id)
                all_logs.append(
                    f"--- Job: {job_name} (id={job_id}) — logs not available (HTTP {log_resp.status_code}) ---"
                )

        if not all_logs:
            return "No failure details found in jobs"

        # Truncate per-job to keep the LLM payload bounded; parse_ci_logs then
        # extracts only error-context lines.
        raw = "\n".join(log[:8000] for log in all_logs)
        return parse_ci_logs(raw)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_run_info(self, owner: str, repo: str, run_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/api/v1/repos/{owner}/{repo}/actions/runs/{run_id}"
        resp = await self._client.get(url, headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def list_runs(self, owner: str, repo: str, branch: str, limit: int = 1) -> list[dict[str, Any]]:
        url = f"{self.base_url}/api/v1/repos/{owner}/{repo}/actions/runs"
        params = {"branch": branch, "limit": limit}
        resp = await self._client.get(url, headers=self._headers(), params=params)
        resp.raise_for_status()
        # Forgejo v16 returns {"total_count": N, "workflow_runs": [...]}; some
        # Gitea forks return a bare array. Accept both.
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("workflow_runs", []) if isinstance(data, dict) else []

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def list_repos(self, org: str) -> list[str]:
        try:
            url = f"{self.base_url}/api/v1/orgs/{org}/repos"
            params = {"limit": 100}
            resp = await self._client.get(url, headers=self._headers(), params=params)
            resp.raise_for_status()
            return [r["full_name"] for r in resp.json()]
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                raise
        url = f"{self.base_url}/api/v1/users/{org}/repos"
        params = {"limit": 100}
        resp = await self._client.get(url, headers=self._headers(), params=params)
        resp.raise_for_status()
        return [r["full_name"] for r in resp.json()]


def create_ci_client(platform: str, token: str, base_url: str) -> CIClient:
    if platform == "github":
        return GitHubCIClient(token=token, base_url="https://api.github.com")
    elif platform == "forgejo":
        return ForgejoCIClient(token=token, base_url=base_url)
    raise ValueError(f"Unknown CI platform: {platform}")
