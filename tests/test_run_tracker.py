from __future__ import annotations

import asyncio

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
