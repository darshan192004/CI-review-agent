from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import aiosqlite

from config import settings

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
                session_id INTEGER,
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
        # Migration: add missing columns (must run BEFORE the session index so
        # the index never references a column that doesn't exist yet).
        async with db.execute("PRAGMA table_info(ci_runs)") as cursor:
            columns = [row[1] async for row in cursor]
            if "last_webhook_at" not in columns:
                await db.execute("ALTER TABLE ci_runs ADD COLUMN last_webhook_at REAL DEFAULT 0")
            if "attempt_count" not in columns:
                await db.execute("ALTER TABLE ci_runs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0")
            if "session_id" not in columns:
                await db.execute("ALTER TABLE ci_runs ADD COLUMN session_id INTEGER")
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ci_runs_session
            ON ci_runs (session_id)
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS ci_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repository TEXT NOT NULL,
                branch TEXT DEFAULT '',
                head_sha TEXT NOT NULL,
                trigger_run_id TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 1,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                status TEXT NOT NULL DEFAULT 'active',
                previous_analysis TEXT DEFAULT '',
                last_fix_sha TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(repository, head_sha)
            )
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ci_sessions_repo_head
            ON ci_sessions (repository, head_sha)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ci_sessions_fix_sha
            ON ci_sessions (repository, last_fix_sha)
            """
        )
        await db.commit()


class RunTracker:
    def __init__(self, ttl_seconds: int = 3600, max_attempts: int = 3) -> None:
        self._ttl = ttl_seconds
        self._max_attempts = max_attempts
        self._lock = asyncio.Lock()

    def _make_key(self, repository: str, run_id: str, run_attempt: str = "1") -> str:
        return f"{repository}:{run_id}:{run_attempt}"

    async def _evict_expired(self, db: aiosqlite.Connection) -> None:
        now = time.time()
        await db.execute(
            "DELETE FROM ci_runs WHERE ? - updated_at > ?",
            (now, self._ttl),
        )
        await db.commit()

    async def is_duplicate(self, repository: str, run_id: str, run_attempt: str = "1") -> bool:
        status = await self.get_run_status(repository, run_id, run_attempt)
        if status is None:
            return False
        # 'error' is deliberately NOT terminal here: it means the agent crashed
        # (e.g. missing LLM credentials), not that the CI run concluded. A fresh
        # webhook for the same run must re-enter the pipeline so a re-run or
        # redelivery can recover once the config is fixed.
        return status in (
            "PASSED",
            "EXHAUSTED",
            "CANNOT_FIX",
            "FIX_PUSHED",
            "AGENT_WORKING",
            "success",
        )

    async def get_run_status(self, repository: str, run_id: str, run_attempt: str = "1") -> str | None:
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

    async def is_completed(self, repository: str, run_id: str, run_attempt: str = "1") -> bool:
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
        session_id: int | None = None,
        created_at: float | None = None,
        force_created_at: bool = False,
    ) -> None:
        now = time.time()
        created = created_at if created_at is not None else now
        update_clauses = [
            "status = excluded.status",
            "platform = excluded.platform",
            "branch = excluded.branch",
            "commit_sha = excluded.commit_sha",
            "author = excluded.author",
            "failure_summary = excluded.failure_summary",
            "patch_summary = excluded.patch_summary",
            "attempt_count = excluded.attempt_count",
            "session_id = COALESCE(excluded.session_id, ci_runs.session_id)",
            "updated_at = excluded.updated_at",
        ]
        if force_created_at:
            update_clauses.insert(0, "created_at = excluded.created_at")
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await self._evict_expired(db)
            await db.execute(
                "INSERT INTO ci_runs ("
                " repository, run_id, run_attempt, status, platform, branch,"
                " commit_sha, author, failure_summary, patch_summary,"
                " attempt_count, created_at, updated_at, session_id"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(repository, run_id, run_attempt) DO UPDATE SET " + ", ".join(update_clauses),
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
                    created,
                    now,
                    session_id,
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
        session_id: int | None = None,
    ) -> None:
        now = time.time()
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await db.execute(
                """
                UPDATE ci_runs
                SET status = ?, branch = ?, commit_sha = ?, author = ?,
                    failure_summary = ?, patch_summary = ?, attempt_count = ?, updated_at = ?,
                    session_id = COALESCE(?, session_id)
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
                    session_id,
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
                "created_at, updated_at, session_id FROM ci_runs "
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
                "created_at, updated_at, session_id FROM ci_runs "
                "ORDER BY created_at DESC, CAST(run_id AS INTEGER) DESC"
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def get_run(
        self,
        repository: str,
        run_id: str,
        run_attempt: str = "1",
    ) -> dict[str, Any] | None:
        """Fetch a single run (used by the SSE renderer to show run time)."""
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await self._evict_expired(db)
            async with db.execute(
                "SELECT repository, run_id, run_attempt, status, platform, branch, "
                "commit_sha, author, failure_summary, patch_summary, attempt_count, "
                "created_at, updated_at, session_id FROM ci_runs "
                "WHERE repository = ? AND run_id = ? AND run_attempt = ?",
                (repository, run_id, run_attempt),
            ) as cursor:
                row = await cursor.fetchone()
        return self._row_to_dict(row) if row else None

    async def get_run_by_session(self, repository: str, session_id: int) -> dict[str, Any] | None:
        """Fetch the lifecycle row bound to a session (used by the SSE renderer).

        A session may accrue multiple ci_runs rows (the original failing run plus
        the bot's re-run); the lifecycle row is the most recently created one.
        """
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await self._evict_expired(db)
            async with db.execute(
                "SELECT repository, run_id, run_attempt, status, platform, branch, "
                "commit_sha, author, failure_summary, patch_summary, attempt_count, "
                "created_at, updated_at, session_id FROM ci_runs "
                "WHERE repository = ? AND session_id = ? ORDER BY created_at DESC LIMIT 1",
                (repository, session_id),
            ) as cursor:
                row = await cursor.fetchone()
        return self._row_to_dict(row) if row else None

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
                async with db.execute("SELECT status, COUNT(*) FROM ci_runs GROUP BY status") as cursor:
                    async for row in cursor:
                        counts[row[0]] = row[1]
            return counts

    async def clear(self) -> None:
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await db.execute("DELETE FROM ci_runs")
            await db.commit()

    async def clear_sessions(self) -> None:
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await db.execute("DELETE FROM ci_sessions")
            await db.commit()

    async def create_session(
        self,
        repository: str,
        head_sha: str,
        branch: str = "",
        trigger_run_id: str = "",
        *,
        max_attempts: int = 0,
    ) -> dict[str, Any] | None:
        """Create an active retry session keyed by the failing human head_sha.

        Returns the existing session if one is already active for this head_sha
        (idempotent duplicate webhook delivery); returns None if a terminal
        session already exists for it (never resurrect completed sessions).
        """
        now = time.time()
        limit = max_attempts or self._max_attempts
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await db.execute(
                """
                INSERT INTO ci_sessions (
                    repository, branch, head_sha, trigger_run_id,
                    attempt_count, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, 'active', ?, ?)
                ON CONFLICT(repository, head_sha) DO NOTHING
                """,
                (repository, branch, head_sha, trigger_run_id, now, now),
            )
            async with db.execute(
                "SELECT * FROM ci_sessions WHERE repository = ? AND head_sha = ?",
                (repository, head_sha),
            ) as cursor:
                row = await cursor.fetchone()
            await db.commit()
        if row is None:
            return None
        session = self._session_row_to_dict(row)
        if session["status"] != "active":
            logger.info(
                "Session for %s @ %s already terminal (%s) — not resurrecting",
                repository,
                head_sha,
                session["status"],
            )
            return None
        if session["max_attempts"] != limit:
            await self.update_session(session["id"], max_attempts=limit)
            session["max_attempts"] = limit
        return session

    async def get_session(self, session_id: int) -> dict[str, Any] | None:
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            async with db.execute("SELECT * FROM ci_sessions WHERE id = ?", (session_id,)) as cursor:
                row = await cursor.fetchone()
        return self._session_row_to_dict(row) if row else None

    async def get_session_by_head_sha(self, repository: str, head_sha: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            async with db.execute(
                "SELECT * FROM ci_sessions WHERE repository = ? AND head_sha = ?",
                (repository, head_sha),
            ) as cursor:
                row = await cursor.fetchone()
        return self._session_row_to_dict(row) if row else None

    async def get_session_by_fix_sha(self, repository: str, fix_sha: str) -> dict[str, Any] | None:
        """Find the active session whose bot-pushed head_sha matches a webhook.

        This is the lineage link that lets a bot-authored terminal webhook be
        consumed: the agent records `last_fix_sha` when it pushes a fix, and a
        bot webhook for that same commit resolves back to its originating
        session. Only 'active' sessions match — terminal sessions reject late
        or duplicate bot webhooks.
        """
        if not fix_sha:
            return None
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            async with db.execute(
                "SELECT * FROM ci_sessions "
                "WHERE repository = ? AND last_fix_sha = ? AND status = 'active' "
                "ORDER BY updated_at DESC LIMIT 1",
                (repository, fix_sha),
            ) as cursor:
                row = await cursor.fetchone()
        return self._session_row_to_dict(row) if row else None

    async def update_session(
        self,
        session_id: int,
        *,
        attempt_count: int | None = None,
        status: str | None = None,
        previous_analysis: str | None = None,
        last_fix_sha: str | None = None,
        max_attempts: int | None = None,
    ) -> None:
        now = time.time()
        sets: list[str] = []
        values: list[Any] = []
        if attempt_count is not None:
            sets.append("attempt_count = ?")
            values.append(attempt_count)
        if status is not None:
            sets.append("status = ?")
            values.append(status)
        if previous_analysis is not None:
            sets.append("previous_analysis = ?")
            values.append(previous_analysis)
        if last_fix_sha is not None:
            sets.append("last_fix_sha = ?")
            values.append(last_fix_sha)
        if max_attempts is not None:
            sets.append("max_attempts = ?")
            values.append(max_attempts)
        if not sets:
            return
        sets.append("updated_at = ?")
        values.append(now)
        values.append(session_id)
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            await db.execute(
                f"UPDATE ci_sessions SET {', '.join(sets)} WHERE id = ?",
                values,
            )
            await db.commit()

    async def get_all_sessions(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(_DB_PATH) as db:
            await _init_db(_DB_PATH)
            async with db.execute("SELECT * FROM ci_sessions ORDER BY created_at ASC") as cursor:
                rows = await cursor.fetchall()
        return [self._session_row_to_dict(row) for row in rows]

    def _session_row_to_dict(self, row: Any) -> dict[str, Any]:
        return {
            "id": row[0],
            "repository": row[1],
            "branch": row[2],
            "head_sha": row[3],
            "trigger_run_id": row[4],
            "attempt_count": row[5],
            "max_attempts": row[6],
            "status": row[7],
            "previous_analysis": row[8],
            "last_fix_sha": row[9],
            "created_at": row[10],
            "updated_at": row[11],
        }

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
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
            "session_id": row[13],
        }


run_tracker = RunTracker(ttl_seconds=max(60, settings.run_history_ttl_hours * 3600))
