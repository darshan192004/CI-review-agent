from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any

import git
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class GitError(Exception):
    pass


class GitManager:
    def __init__(self, repo_path: str = ".") -> None:
        self.repo_path = Path(repo_path).resolve()

    def _get_repo(self) -> git.Repo:
        try:
            return git.Repo(self.repo_path)
        except git.InvalidGitRepositoryError as e:
            raise GitError(f"Not a valid git repository: {self.repo_path}") from e

    async def reset_clean(self) -> None:
        repo = self._get_repo()
        await asyncio.to_thread(repo.git.reset, "--hard", "HEAD")
        await asyncio.to_thread(repo.git.clean, "-fd")
        logger.info("Git workspace cleaned: %s", self.repo_path)

    async def get_file_content(self, file_path: str) -> str:
        full_path = self.repo_path / file_path
        if not full_path.exists():
            raise GitError(f"File not found: {file_path}")

        def _read() -> str:
            return full_path.read_text(encoding="utf-8")

        return await asyncio.to_thread(_read)

    async def apply_patch(self, diff: str) -> bool:
        repo = self._get_repo()

        def _apply() -> bool:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".patch", delete=False, encoding="utf-8"
            ) as f:
                f.write(diff)
                patch_path = f.name
            try:
                repo.git.apply(patch_path)
                return True
            except git.GitCommandError:
                try:
                    repo.git.apply("--3way", patch_path)
                    return True
                except git.GitCommandError as e:
                    logger.error("Failed to apply patch: %s", e)
                    return False
            finally:
                Path(patch_path).unlink(missing_ok=True)

        return await asyncio.to_thread(_apply)

    async def commit(self, message: str) -> str:
        repo = self._get_repo()

        def _commit() -> str:
            repo.git.add(A=True)
            repo.index.commit(message)
            head = repo.head.commit
            logger.info("Committed: %s (%s)", message, head.hexsha[:8])
            return head.hexsha

        return await asyncio.to_thread(_commit)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def push(self, branch: str) -> None:
        repo = self._get_repo()

        def _push() -> None:
            origin = repo.remote("origin")
            origin.push(refspec=f"HEAD:{branch}")

        await asyncio.to_thread(_push)
        logger.info("Pushed to origin/%s", branch)

    async def get_diff(self) -> str:
        repo = self._get_repo()

        def _diff() -> str:
            return repo.git.diff("HEAD")

        return await asyncio.to_thread(_diff)
