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
                status TEXT NOT NULL DEFAULT 'processing',
                platform TEXT DEFAULT '',
                branch TEXT DEFAULT '',
                commit_sha TEXT DEFAULT '',
                author TEXT DEFAULT '',
                failure_summary TEXT DEFAULT '',
                patch_summary TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(repository, run_id)
            )
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ci_runs_repo_run
            ON ci_runs (repository, run_id)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ci_runs_status
            ON ci_runs (status)
            """
        )
        await db.commit()


class RunTracker:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()

    def _make_key(self, repository: str, run_id: str) -> str:
        return f"{repository}:{run_id}"

    async def _evict_expired(self, db: aiosqlite.Connection) -> None:
        now = time.monotonic()
        await db.execute(
            "DELETE FROM ci_runs WHERE ? - updated_at > ?",
            (now, self._ttl),
        )
        await db.commit()

    async def is_duplicate(self, repository: str, run_id: str) -> bool:
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await self._evict_expired(db)
            async with db.execute(
                "SELECT 1 FROM ci_runs WHERE repository = ? AND run_id = ?",
                (repository, run_id),
            ) as cursor:
                row = await cursor.fetchone()
                return row is not None

    async def record(
        self,
        repository: str,
        run_id: str,
        status: str = "processing",
        platform: str = "",
        branch: str = "",
        commit_sha: str = "",
        author: str = "",
        failure_summary: str = "",
        patch_summary: str = "",
    ) -> None:
        now = time.monotonic()
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await self._evict_expired(db)
            await db.execute(
                """
                INSERT INTO ci_runs (
                    repository, run_id, status, platform, branch,
                    commit_sha, author, failure_summary, patch_summary,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository, run_id) DO UPDATE SET
                    status = excluded.status,
                    platform = excluded.platform,
                    branch = excluded.branch,
                    commit_sha = excluded.commit_sha,
                    author = excluded.author,
                    failure_summary = excluded.failure_summary,
                    patch_summary = excluded.patch_summary,
                    updated_at = excluded.updated_at
                """,
                (
                    repository,
                    run_id,
                    status,
                    platform,
                    branch,
                    commit_sha,
                    author,
                    failure_summary,
                    patch_summary,
                    now,
                    now,
                ),
            )
            await db.commit()
        logger.info(
            "Recorded run: %s (status=%s, platform=%s)",
            self._make_key(repository, run_id),
            status,
            platform,
        )

    async def update_status(
        self,
        repository: str,
        run_id: str,
        status: str,
        branch: str = "",
        commit_sha: str = "",
        author: str = "",
        failure_summary: str = "",
        patch_summary: str = "",
    ) -> None:
        now = time.monotonic()
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await db.execute(
                """
                UPDATE ci_runs
                SET status = ?, branch = ?, commit_sha = ?, author = ?,
                    failure_summary = ?, patch_summary = ?, updated_at = ?
                WHERE repository = ? AND run_id = ?
                """,
                (
                    status,
                    branch,
                    commit_sha,
                    author,
                    failure_summary,
                    patch_summary,
                    now,
                    repository,
                    run_id,
                ),
            )
            await db.commit()

    async def get_active_runs(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await self._evict_expired(db)
            async with db.execute(
                "SELECT repository, run_id, status, platform, branch, "
                "commit_sha, author, failure_summary, patch_summary, "
                "created_at, updated_at FROM ci_runs WHERE status IN ('processing', 'AGENT_WORKING')"
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def get_all_runs(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await self._evict_expired(db)
            async with db.execute(
                "SELECT repository, run_id, status, platform, branch, "
                "commit_sha, author, failure_summary, patch_summary, "
                "created_at, updated_at FROM ci_runs ORDER BY created_at ASC"
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def count_by_status(self) -> dict[str, int]:
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await self._evict_expired(db)
            counts: dict[str, int] = {}
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
            "status": row[2],
            "platform": row[3],
            "branch": row[4],
            "commit_sha": row[5],
            "author": row[6],
            "failure_summary": row[7],
            "patch_summary": row[8],
            "created_at": row[9],
            "updated_at": row[10],
        }


run_tracker = RunTracker()
