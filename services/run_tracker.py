from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_DB_PATH = Path(".ci_runs.db")


async def _init_db(db_path: Path) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS ci_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repository TEXT NOT NULL,
                run_id TEXT NOT NULL,
                run_attempt TEXT NOT NULL DEFAULT '1',
                status TEXT NOT NULL DEFAULT 'processing',
                platform TEXT DEFAULT '',
                branch TEXT DEFAULT '',
                commit_sha TEXT DEFAULT '',
                author TEXT DEFAULT '',
                failure_summary TEXT DEFAULT '',
                patch_summary TEXT DEFAULT '',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_webhook_at REAL DEFAULT 0,
                UNIQUE(repository, run_id, run_attempt)
            )
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ci_runs_repo_run
            ON ci_runs (repository, run_id, run_attempt)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ci_runs_status
            ON ci_runs (status)
            """
        )
        # Migration: add last_webhook_at column if missing
        async with db.execute("PRAGMA table_info(ci_runs)") as cursor:
            columns = [row[1] async for row in cursor]
            if "last_webhook_at" not in columns:
                await db.execute(
                    "ALTER TABLE ci_runs ADD COLUMN last_webhook_at REAL DEFAULT 0"
                )
            if "attempt_count" not in columns:
                await db.execute(
                    "ALTER TABLE ci_runs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"
                )
        await db.commit()


class RunTracker:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()

    def _make_key(self, repository: str, run_id: str, run_attempt: str = "1") -> str:
        return f"{repository}:{run_id}:{run_attempt}"

    async def _evict_expired(self, db: aiosqlite.Connection) -> None:
        now = time.monotonic()
        await db.execute(
            "DELETE FROM ci_runs WHERE ? - updated_at > ?",
            (now, self._ttl),
        )
        await db.commit()

    async def is_duplicate(
        self, repository: str, run_id: str, run_attempt: str = "1"
    ) -> bool:
        status = await self.get_run_status(repository, run_id, run_attempt)
        if status is None:
            return False
        return status in (
            "PASSED", "EXHAUSTED", "AGENT_WORKING", "error", "success",
        )

    async def get_run_status(
        self, repository: str, run_id: str, run_attempt: str = "1"
    ) -> str | None:
        """Get the current status of a run, or None if not tracked."""
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await self._evict_expired(db)
            async with db.execute(
                "SELECT status FROM ci_runs WHERE repository = ? AND run_id = ? AND run_attempt = ?",
                (repository, run_id, run_attempt),
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def is_completed(
        self, repository: str, run_id: str, run_attempt: str = "1"
    ) -> bool:
        """Check if a run has already completed (used to detect reruns)."""
        status = await self.get_run_status(repository, run_id, run_attempt)
        return status in ("PASSED", "FAILED", "EXHAUSTED", "error", "success", "failed")

    async def record(
        self,
        repository: str,
        run_id: str,
        run_attempt: str = "1",
        status: str = "processing",
        platform: str = "",
        branch: str = "",
        commit_sha: str = "",
        author: str = "",
        failure_summary: str = "",
        patch_summary: str = "",
        attempt_count: int = 0,
    ) -> None:
        now = time.monotonic()
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await self._evict_expired(db)
            await db.execute(
                """
                INSERT INTO ci_runs (
                    repository, run_id, run_attempt, status, platform, branch,
                    commit_sha, author, failure_summary, patch_summary,
                    attempt_count,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository, run_id, run_attempt) DO UPDATE SET
                    status = excluded.status,
                    platform = excluded.platform,
                    branch = excluded.branch,
                    commit_sha = excluded.commit_sha,
                    author = excluded.author,
                    failure_summary = excluded.failure_summary,
                    patch_summary = excluded.patch_summary,
                    attempt_count = excluded.attempt_count,
                    updated_at = excluded.updated_at
                """,
                (
                    repository,
                    run_id,
                    run_attempt,
                    status,
                    platform,
                    branch,
                    commit_sha,
                    author,
                    failure_summary,
                    patch_summary,
                    attempt_count,
                    now,
                    now,
                ),
            )
            await db.commit()
        logger.info(
            "Recorded run: %s (status=%s, platform=%s)",
            self._make_key(repository, run_id, run_attempt),
            status,
            platform,
        )

    async def update_status(
        self,
        repository: str,
        run_id: str,
        status: str,
        run_attempt: str = "1",
        branch: str = "",
        commit_sha: str = "",
        author: str = "",
        failure_summary: str = "",
        patch_summary: str = "",
        attempt_count: int = 0,
    ) -> None:
        now = time.monotonic()
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await db.execute(
                """
                UPDATE ci_runs
                SET status = ?, branch = ?, commit_sha = ?, author = ?,
                    failure_summary = ?, patch_summary = ?, attempt_count = ?, updated_at = ?
                WHERE repository = ? AND run_id = ? AND run_attempt = ?
                """,
                (
                    status,
                    branch,
                    commit_sha,
                    author,
                    failure_summary,
                    patch_summary,
                    attempt_count,
                    now,
                    repository,
                    run_id,
                    run_attempt,
                ),
            )
            await db.commit()

    async def get_active_runs(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await self._evict_expired(db)
            async with db.execute(
                "SELECT repository, run_id, run_attempt, status, platform, branch, "
                "commit_sha, author, failure_summary, patch_summary, attempt_count, "
                "created_at, updated_at FROM ci_runs "
                "WHERE status IN ('processing', 'AGENT_WORKING', 'RUNNING', 'PENDING', 'QUEUED', 'WAITING')"
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def get_all_runs(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await self._evict_expired(db)
            async with db.execute(
                "SELECT repository, run_id, run_attempt, status, platform, branch, "
                "commit_sha, author, failure_summary, patch_summary, attempt_count, "
                "created_at, updated_at FROM ci_runs ORDER BY created_at ASC"
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def count_by_status(self, repo: str = "") -> dict[str, int]:
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await self._evict_expired(db)
            counts: dict[str, int] = {}
            if repo:
                async with db.execute(
                    "SELECT status, COUNT(*) FROM ci_runs WHERE repository = ? GROUP BY status",
                    (repo,),
                ) as cursor:
                    async for row in cursor:
                        counts[row[0]] = row[1]
            else:
                async with db.execute(
                    "SELECT status, COUNT(*) FROM ci_runs GROUP BY status"
                ) as cursor:
                    async for row in cursor:
                        counts[row[0]] = row[1]
            return counts

    async def clear(self) -> None:
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await db.execute("DELETE FROM ci_runs")
            await db.commit()

    def _row_to_dict(self, row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "repository": row[0],
            "run_id": row[1],
            "run_attempt": row[2],
            "status": row[3],
            "platform": row[4],
            "branch": row[5],
            "commit_sha": row[6],
            "author": row[7],
            "failure_summary": row[8],
            "patch_summary": row[9],
            "attempt_count": row[10],
            "created_at": row[11],
            "updated_at": row[12],
        }


run_tracker = RunTracker()
