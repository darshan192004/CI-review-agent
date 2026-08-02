from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from services.run_tracker import RunTracker


class TestRunTracker:
    @pytest.fixture(autouse=True)
    def _isolated_db(self, tmp_path):
        import services.run_tracker as rt

        self._original_db = rt._DB_PATH
        rt._DB_PATH = tmp_path / "ci_runs.db"
        self.tracker = RunTracker(ttl_seconds=60)
        yield
        rt._DB_PATH = self._original_db

    @pytest.mark.asyncio
    async def test_is_duplicate_returns_false_for_new_run(self) -> None:
        assert await self.tracker.is_duplicate("owner/repo", "123") is False

    @pytest.mark.asyncio
    async def test_record_and_duplicate(self) -> None:
        await self.tracker.record("owner/repo", "123", status="PASSED")
        assert await self.tracker.is_duplicate("owner/repo", "123") is True

    @pytest.mark.asyncio
    async def test_processing_not_duplicate(self) -> None:
        """A run synced with 'processing' status should NOT block a webhook."""
        await self.tracker.record("owner/repo", "123", status="processing")
        assert await self.tracker.is_duplicate("owner/repo", "123") is False

    @pytest.mark.asyncio
    async def test_failed_not_duplicate(self) -> None:
        """A run synced with 'FAILED' status should NOT block a webhook."""
        await self.tracker.record("owner/repo", "123", status="FAILED")
        assert await self.tracker.is_duplicate("owner/repo", "123") is False

    @pytest.mark.asyncio
    async def test_different_runs_not_duplicates(self) -> None:
        await self.tracker.record("owner/repo", "123", status="PASSED")
        assert await self.tracker.is_duplicate("owner/repo", "456") is False

    @pytest.mark.asyncio
    async def test_different_repos_not_duplicates(self) -> None:
        await self.tracker.record("owner/repo1", "123")
        assert await self.tracker.is_duplicate("owner/repo2", "123") is False

    @pytest.mark.asyncio
    async def test_ttl_expiry(self) -> None:
        tracker = RunTracker(ttl_seconds=0)
        await tracker.record("owner/repo", "123")
        await asyncio.sleep(0.01)
        assert await tracker.is_duplicate("owner/repo", "123") is False

    @pytest.mark.asyncio
    async def test_get_active_runs(self) -> None:
        await self.tracker.record("owner/repo", "1", status="processing")
        await self.tracker.record("owner/repo", "2", status="completed")
        active = await self.tracker.get_active_runs()
        assert len(active) == 1
        assert active[0]["run_id"] == "1"

    @pytest.mark.asyncio
    async def test_count_by_status(self) -> None:
        await self.tracker.record("owner/repo", "1", status="processing")
        await self.tracker.record("owner/repo", "2", status="completed")
        await self.tracker.record("owner/repo", "3", status="error")
        counts = await self.tracker.count_by_status()
        assert counts["processing"] == 1
        assert counts["completed"] == 1
        assert counts["error"] == 1

    @pytest.mark.asyncio
    async def test_update_status(self) -> None:
        await self.tracker.record("owner/repo", "123", status="processing")
        await self.tracker.update_status("owner/repo", "123", "completed")
        runs = await self.tracker.get_all_runs()
        assert runs[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_clear(self) -> None:
        await self.tracker.record("owner/repo", "1")
        await self.tracker.record("owner/repo", "2")
        await self.tracker.clear()
        assert len(await self.tracker.get_all_runs()) == 0

    @pytest.mark.asyncio
    async def test_get_all_runs(self) -> None:
        await self.tracker.record("owner/repo", "1")
        await self.tracker.record("owner/repo", "2")
        assert len(await self.tracker.get_all_runs()) == 2

    @pytest.mark.asyncio
    async def test_get_all_runs_orders_newest_first(self) -> None:
        tracker = RunTracker(ttl_seconds=10**12)
        with patch(
            "services.run_tracker.time.time",
            side_effect=[1000.0, 1000.0, 2000.0, 2000.0, 3000.0, 3000.0],
        ):
            await tracker.record("owner/repo", "1")
            await tracker.record("owner/repo", "2")
            await tracker.record("owner/repo", "3")
        runs = await tracker.get_all_runs()
        assert [r["run_id"] for r in runs] == ["3", "2", "1"]

    @pytest.mark.asyncio
    async def test_get_all_runs_breaks_ties_by_run_id_desc(self) -> None:
        tracker = RunTracker(ttl_seconds=10**12)
        now = 1_700_000_000.0
        for run_id in ("2", "3", "14"):
            await tracker.record("owner/repo", run_id, created_at=now)
        runs = await tracker.get_all_runs()
        assert [r["run_id"] for r in runs] == ["14", "3", "2"]

    @pytest.mark.asyncio
    async def test_record_keeps_original_created_at_on_conflict(self) -> None:
        tracker = RunTracker(ttl_seconds=10**12)
        await tracker.record("owner/repo", "1", created_at=100.0)
        await tracker.record("owner/repo", "1", created_at=200.0)
        runs = await tracker.get_all_runs()
        assert runs[0]["created_at"] == 100.0

    @pytest.mark.asyncio
    async def test_record_force_created_at_overwrites_on_conflict(self) -> None:
        tracker = RunTracker(ttl_seconds=10**12)
        await tracker.record("owner/repo", "1", created_at=100.0)
        await tracker.record("owner/repo", "1", created_at=200.0, force_created_at=True)
        runs = await tracker.get_all_runs()
        assert runs[0]["created_at"] == 200.0

    @pytest.mark.asyncio
    async def test_record_stores_epoch_timestamp(self) -> None:
        tracker = RunTracker(ttl_seconds=10**12)
        with patch("services.run_tracker.time.time", return_value=1_700_000_000.0):
            await tracker.record("owner/repo", "1")
        runs = await tracker.get_all_runs()
        assert runs[0]["created_at"] == 1_700_000_000.0

    @pytest.mark.asyncio
    async def test_get_run_returns_recorded_run(self) -> None:
        await self.tracker.record("owner/repo", "42", run_attempt="2", status="PASSED")
        run = await self.tracker.get_run("owner/repo", "42", "2")
        assert run is not None
        assert run["status"] == "PASSED"
        assert run["run_attempt"] == "2"
        assert run["created_at"] is not None

    @pytest.mark.asyncio
    async def test_get_run_missing_returns_none(self) -> None:
        assert await self.tracker.get_run("owner/nope", "1") is None

    @pytest.mark.asyncio
    async def test_create_session_returns_active_session(self) -> None:
        """create_session must return the created session (was returning None
        because _session_row_to_dict mis-mapped max_attempts/status columns)."""
        session = await self.tracker.create_session("owner/repo", "sha123", branch="main", trigger_run_id="42")
        assert session is not None
        assert session["id"] is not None
        assert session["head_sha"] == "sha123"
        assert session["trigger_run_id"] == "42"
        assert session["status"] == "active"
        assert session["attempt_count"] == 1
        assert session["max_attempts"] == 3
        assert session["last_fix_sha"] == ""

    @pytest.mark.asyncio
    async def test_session_fix_sha_lineage_roundtrip(self) -> None:
        """The fix-sha lineage link: record last_fix_sha on the active session,
        then resolve it via get_session_by_fix_sha (used by bot webhooks)."""
        session = await self.tracker.create_session("owner/repo", "sha123", branch="main", trigger_run_id="42")
        assert session is not None
        await self.tracker.update_session(
            session["id"],
            last_fix_sha="fixsha999",
            previous_analysis="the fix explanation",
            attempt_count=2,
        )
        resolved = await self.tracker.get_session_by_fix_sha("owner/repo", "fixsha999")
        assert resolved is not None
        assert resolved["id"] == session["id"]
        assert resolved["last_fix_sha"] == "fixsha999"
        assert resolved["previous_analysis"] == "the fix explanation"
        assert resolved["attempt_count"] == 2
        assert resolved["status"] == "active"

    @pytest.mark.asyncio
    async def test_session_fix_sha_requires_active(self) -> None:
        """Terminal sessions must not match fix-sha lineage queries."""
        session = await self.tracker.create_session("owner/repo", "sha123", branch="main", trigger_run_id="42")
        assert session is not None
        await self.tracker.update_session(session["id"], last_fix_sha="fixsha999")
        await self.tracker.update_session(session["id"], status="PASSED")
        assert await self.tracker.get_session_by_fix_sha("owner/repo", "fixsha999") is None

    @pytest.mark.asyncio
    async def test_session_update_persists_all_fields(self) -> None:
        """update_session must persist every column it writes (columns were
        silently mis-mapped, so last_fix_sha/status never took effect)."""
        session = await self.tracker.create_session("owner/repo", "sha123", branch="main", trigger_run_id="42")
        assert session is not None
        await self.tracker.update_session(
            session["id"],
            status="EXHAUSTED",
            last_fix_sha="fixsha999",
            previous_analysis="analysis",
            attempt_count=3,
        )
        fresh = await self.tracker.get_session(session["id"])
        assert fresh is not None
        assert fresh["status"] == "EXHAUSTED"
        assert fresh["last_fix_sha"] == "fixsha999"
        assert fresh["previous_analysis"] == "analysis"
        assert fresh["attempt_count"] == 3

    @pytest.mark.asyncio
    async def test_record_with_created_at_passthrough(self) -> None:
        """record() must accept an explicit created_at (CI execution timestamp)
        instead of always stamping 'now'."""
        await self.tracker.record("owner/repo", "1", created_at=1_600_000_000.0)
        run = await self.tracker.get_run("owner/repo", "1")
        assert run is not None
        assert run["created_at"] == 1_600_000_000.0

    @pytest.mark.asyncio
    async def test_record_defaults_created_at_to_now(self) -> None:
        tracker = RunTracker(ttl_seconds=10**12)
        with patch("services.run_tracker.time.time", return_value=1_700_000_000.0):
            await tracker.record("owner/repo", "1")
        run = await tracker.get_run("owner/repo", "1")
        assert run is not None
        assert run["created_at"] == 1_700_000_000.0

    @pytest.mark.asyncio
    async def test_record_stores_session_id(self) -> None:
        await self.tracker.record("owner/repo", "1", session_id=7)
        run = await self.tracker.get_run("owner/repo", "1")
        assert run is not None
        assert run["session_id"] == 7

    @pytest.mark.asyncio
    async def test_record_session_id_defaults_null(self) -> None:
        await self.tracker.record("owner/repo", "1")
        run = await self.tracker.get_run("owner/repo", "1")
        assert run is not None
        assert run["session_id"] is None

    @pytest.mark.asyncio
    async def test_update_status_binds_session_id(self) -> None:
        """update_status() must be able to bind a row to its session (the
        session is only created AFTER the initial record)."""
        await self.tracker.record("owner/repo", "1", status="processing")
        await self.tracker.update_status("owner/repo", "1", "AGENT_WORKING", session_id=9)
        run = await self.tracker.get_run("owner/repo", "1")
        assert run is not None
        assert run["session_id"] == 9

    @pytest.mark.asyncio
    async def test_get_run_by_session_returns_bound_row(self) -> None:
        await self.tracker.record("owner/repo", "1", status="FIX_PUSHED", session_id=5)
        run = await self.tracker.get_run_by_session("owner/repo", 5)
        assert run is not None
        assert run["run_id"] == "1"
        assert run["status"] == "FIX_PUSHED"

    @pytest.mark.asyncio
    async def test_get_run_by_session_missing_returns_none(self) -> None:
        await self.tracker.record("owner/repo", "1", session_id=5)
        assert await self.tracker.get_run_by_session("owner/repo", 999) is None

    @pytest.mark.asyncio
    async def test_get_run_by_session_latest_bound_row_wins(self) -> None:
        """A session may accrue multiple ci_runs rows (original + bot runs);
        the lifecycle row is the most recently created one."""
        await self.tracker.record("owner/repo", "1", status="FIX_PUSHED", session_id=5, created_at=100.0)
        await self.tracker.record("owner/repo", "2", status="PASSED", session_id=5, created_at=200.0)
        run = await self.tracker.get_run_by_session("owner/repo", 5)
        assert run is not None
        assert run["run_id"] == "2"
        assert run["status"] == "PASSED"

    @pytest.mark.asyncio
    async def test_init_db_adds_session_id_to_legacy_table(self, tmp_path) -> None:
        """An existing database (created before session_id existed) must be
        migrated in place, mirroring the last_webhook_at migration."""
        import aiosqlite

        import services.run_tracker as rt

        legacy = tmp_path / "legacy.db"
        async with aiosqlite.connect(legacy) as db:
            await db.execute(
                """
                CREATE TABLE ci_runs (
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
            await db.commit()
        with patch.object(rt, "_DB_PATH", legacy):
            await rt._init_db(legacy)
            async with aiosqlite.connect(legacy) as db, db.execute("PRAGMA table_info(ci_runs)") as cursor:
                columns = [row[1] async for row in cursor]
        assert "session_id" in columns

    @pytest.mark.asyncio
    async def test_record_with_extra_fields(self) -> None:
        await self.tracker.record(
            "owner/repo",
            "42",
            status="processing",
            platform="forgejo",
            branch="main",
            commit_sha="abc123",
            author="darshan",
            failure_summary="test failure",
            patch_summary="test patch",
        )
        runs = await self.tracker.get_all_runs()
        run = runs[0]
        assert run["platform"] == "forgejo"
        assert run["branch"] == "main"
        assert run["commit_sha"] == "abc123"
        assert run["author"] == "darshan"
        assert run["failure_summary"] == "test failure"
        assert run["patch_summary"] == "test patch"
