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
        await self.tracker.record("owner/repo", "123")
        assert await self.tracker.is_duplicate("owner/repo", "123") is True

    @pytest.mark.asyncio
    async def test_different_runs_not_duplicates(self) -> None:
        await self.tracker.record("owner/repo", "123")
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
