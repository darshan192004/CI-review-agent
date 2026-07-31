from __future__ import annotations

import logging
import os
import shutil
import tempfile
from typing import Any

import git

from config import settings

logger = logging.getLogger(__name__)

FORBIDDEN_PATHS: tuple[str, ...] = (
    ".github/",
    ".gitlab-ci.yml",
    ".circleci/",
    "Jenkinsfile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".env",
    ".env.local",
    ".env.production",
    ".gitignore",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "go.sum",
    "Makefile",
    "Makefile.*",
    "*.tf",
    "*.tfvars",
    "terragrunt.hcl",
)


class GitError(Exception):
    pass


class WorkspaceGitManager:
    """Context manager for safely cloning, patching, and pushing git repository fixes
    in an isolated temporary directory."""

    def __init__(
        self,
        clone_url: str,
        token: str,
        branch: str,
        commit_sha: str = "",
        depth: int = 1,
    ) -> None:
        if not clone_url:
            raise GitError("No clone URL provided — cannot clone repository")
        if not token:
            logger.warning(
                "No token provided for clone URL %s — clone will likely fail "
                "with authentication error",
                clone_url,
            )

        if "://" in clone_url:
            proto, rest = clone_url.split("://", 1)
            self.authenticated_url = f"{proto}://{token}@{rest}"
        else:
            self.authenticated_url = clone_url

        url_scheme = clone_url.split("://")[0] if "://" in clone_url else "unknown"
        logger.info("Clone config: has_token=%s, url_scheme=%s", bool(token), url_scheme)

        self.branch = branch
        self.commit_sha = commit_sha
        self.depth = depth
        self.temp_dir: str | None = None
        self.repo: git.Repo | None = None

    def __enter__(self) -> WorkspaceGitManager:
        self.temp_dir = tempfile.mkdtemp(prefix="ci_agent_ws_")
        logger.info("Created workspace: %s", self.temp_dir)

        clone_kwargs: dict[str, Any] = {"branch": self.branch}
        if self.depth > 0:
            clone_kwargs["depth"] = self.depth

        try:
            self.repo = git.Repo.clone_from(
                self.authenticated_url,
                self.temp_dir,
                **clone_kwargs,
            )
        except git.GitCommandError as e:
            stderr = getattr(e, "stderr", "") or str(e)
            if "Authentication failed" in stderr or "403" in stderr or "401" in stderr:
                raise GitError(
                    f"Git clone authentication failed for branch '{self.branch}'. "
                    "The token may be missing, expired, or lacks permissions. "
                    "Check FORGEJO_TOKEN / GITHUB_TOKEN configuration."
                ) from e
            if "not found" in stderr.lower() or "404" in stderr:
                raise GitError(
                    f"Repository not found at the provided clone URL (branch='{self.branch}'). "
                    "Check that the repository and branch exist."
                ) from e
            raise GitError(
                f"Git clone failed for branch '{self.branch}': {stderr[:500]}"
            ) from e

        with self.repo.config_writer() as config:
            config.set_value(
                "user", "name", settings.ci_bot_username or "CI Review Bot"
            )
            config.set_value(
                "user", "email", settings.ci_bot_email or "ci-bot@autofix.internal"
            )

        logger.info("Cloned %s (branch=%s, depth=%s)", self.branch, self.branch, self.depth)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            logger.info("Cleaned up workspace: %s", self.temp_dir)

    def apply_file_changes(self, modified_files: list[dict[str, str]]) -> bool:
        """Overwrites file contents directly based on structured LLM output.
        Prevents directory traversal and blocks forbidden paths (CI configs, Dockerfiles, etc.)."""
        try:
            for file_info in modified_files:
                rel_path = file_info["file_path"].lstrip("/")

                full_path = os.path.abspath(os.path.join(self.temp_dir, rel_path))
                if not full_path.startswith(os.path.abspath(self.temp_dir)):
                    raise ValueError(f"Directory traversal detected: {rel_path}")

                if self._is_forbidden_path(rel_path):
                    logger.warning("Skipping forbidden path: %s", rel_path)
                    continue

                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(file_info["content"])

            return True
        except Exception as e:
            logger.error("Failed to apply file changes: %s", e)
            return False

    @staticmethod
    def _is_forbidden_path(rel_path: str) -> bool:
        normalized = rel_path.replace("\\", "/")
        for pattern in FORBIDDEN_PATHS:
            if pattern.endswith("/"):
                if normalized.startswith(pattern):
                    return True
            elif pattern.startswith("*."):
                if normalized.endswith(pattern[1:]):
                    return True
            elif normalized == pattern:
                return True
        return False

    def get_head_sha(self) -> str:
        if self.repo is None:
            raise GitError("Repository not cloned yet")
        return self.repo.head.commit.hexsha

    def commit_and_push(self, commit_message: str) -> bool:
        """Stages all changes, creates a commit, and pushes to remote.
        Only pushes to the original branch set at clone time (branch isolation)."""
        try:
            if not self.repo or not self.repo.is_dirty(untracked_files=True):
                logger.info("No changes detected to commit.")
                return False

            self.repo.git.add(A=True)
            self.repo.index.commit(commit_message)

            origin = self.repo.remote(name="origin")
            origin.push(refspec=f"{self.branch}:{self.branch}")
            logger.info("Pushed fixes to branch %s", self.branch)
            return True
        except Exception as e:
            logger.error("Commit and push failed: %s", e)
            return False


# Backward compatibility alias — removed in Task 6 when nodes.py is rewritten
GitManager = WorkspaceGitManager
