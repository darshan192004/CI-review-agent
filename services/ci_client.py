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
    async def trigger_workflow(
        self, owner: str, repo: str, workflow_id: str, branch: str, commit_sha: str
    ) -> str: ...

    @abc.abstractmethod
    async def poll_status(
        self,
        owner: str,
        repo: str,
        run_id: str,
        interval: int = 30,
        max_wait: int = 600,
    ) -> str: ...

    @abc.abstractmethod
    async def get_run_info(
        self, owner: str, repo: str, run_id: str
    ) -> dict[str, Any]: ...


class GitHubCIClient(CIClient):
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def fetch_logs(self, owner: str, repo: str, run_id: str) -> str:
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}"
        resp = await self._client.get(url, headers=self._headers())
        resp.raise_for_status()
        run_data = resp.json()
        log_url = run_data.get("jobs_url", "")
        if not log_url:
            raise CIClientError(f"No jobs URL found for run {run_id}")

        jobs_resp = await self._client.get(log_url, headers=self._headers())
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
    async def trigger_workflow(
        self, owner: str, repo: str, workflow_id: str, branch: str, commit_sha: str
    ) -> str:
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches"
        payload = {"ref": branch, "inputs": {"commit_sha": commit_sha}}
        resp = await self._client.post(url, json=payload, headers=self._headers())
        if resp.status_code == 204:
            logger.info("Triggered workflow %s on %s/%s", workflow_id, owner, repo)
            return f"Triggered workflow {workflow_id}"
        resp.raise_for_status()
        return resp.text

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def poll_status(
        self,
        owner: str,
        repo: str,
        run_id: str,
        interval: int = 30,
        max_wait: int = 600,
    ) -> str:
        import asyncio

        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}"
        elapsed = 0
        while elapsed < max_wait:
            resp = await self._client.get(url, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "unknown")
            conclusion = data.get("conclusion", "")

            if status == "completed":
                return "PASSED" if conclusion == "success" else "FAILED"

            await asyncio.sleep(interval)
            elapsed += interval

        raise CIClientError(f"CI run {run_id} did not complete within {max_wait}s")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_run_info(self, owner: str, repo: str, run_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}"
        resp = await self._client.get(url, headers=self._headers())
        resp.raise_for_status()
        return resp.json()


class ForgejoCIClient(CIClient):
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"token {self.token}",
            "Accept": "application/json",
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def fetch_logs(self, owner: str, repo: str, run_id: str) -> str:
        url = f"{self.base_url}/api/v1/repos/{owner}/{repo}/actions/runs/{run_id}"
        resp = await self._client.get(url, headers=self._headers())
        resp.raise_for_status()
        data = resp.json()
        jobs = data.get("jobs", [])

        all_logs: list[str] = []
        for job in jobs:
            for step in job.get("steps", []):
                name = step.get("name", "unknown")
                status = step.get("status", "")
                if status == "failure":
                    all_logs.append(f"FAILED STEP: {name}")
                    log_url = step.get("log_url", "")
                    if log_url:
                        log_resp = await self._client.get(
                            log_url, headers=self._headers()
                        )
                        if log_resp.status_code == 200:
                            all_logs.append(log_resp.text[:2000])

        raw = "\n".join(all_logs) if all_logs else "No failure details found"
        return parse_ci_logs(raw)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def trigger_workflow(
        self, owner: str, repo: str, workflow_id: str, branch: str, commit_sha: str
    ) -> str:
        url = f"{self.base_url}/api/v1/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches"
        payload = {"ref": branch}
        resp = await self._client.post(url, json=payload, headers=self._headers())
        resp.raise_for_status()
        logger.info("Triggered Forgejo workflow %s on %s/%s", workflow_id, owner, repo)
        return f"Triggered workflow {workflow_id}"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def poll_status(
        self,
        owner: str,
        repo: str,
        run_id: str,
        interval: int = 30,
        max_wait: int = 600,
    ) -> str:
        import asyncio

        url = f"{self.base_url}/api/v1/repos/{owner}/{repo}/actions/runs/{run_id}"
        elapsed = 0
        while elapsed < max_wait:
            resp = await self._client.get(url, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "unknown")

            if status == "completed":
                conclusion = data.get("conclusion", "")
                return "PASSED" if conclusion == "success" else "FAILED"

            await asyncio.sleep(interval)
            elapsed += interval

        raise CIClientError(f"Forgejo run {run_id} did not complete within {max_wait}s")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_run_info(self, owner: str, repo: str, run_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/api/v1/repos/{owner}/{repo}/actions/runs/{run_id}"
        resp = await self._client.get(url, headers=self._headers())
        resp.raise_for_status()
        return resp.json()


def create_ci_client(platform: str, token: str, base_url: str) -> CIClient:
    if platform == "github":
        return GitHubCIClient(token=token, base_url="https://api.github.com")
    elif platform == "forgejo":
        return ForgejoCIClient(token=token, base_url=base_url)
    raise ValueError(f"Unknown CI platform: {platform}")
