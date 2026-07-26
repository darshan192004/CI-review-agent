from __future__ import annotations

import json
import time
from typing import Any

from services.run_tracker import RunTracker


class TestRunTracker:
    def test_is_duplicate_returns_false_for_new_run(self) -> None:
        tracker = RunTracker(ttl_seconds=60)
        assert tracker.is_duplicate("owner/repo", "123") is False

    def test_record_and_duplicate(self) -> None:
        tracker = RunTracker(ttl_seconds=60)
        tracker.record("owner/repo", "123")
        assert tracker.is_duplicate("owner/repo", "123") is True

    def test_different_runs_not_duplicates(self) -> None:
        tracker = RunTracker(ttl_seconds=60)
        tracker.record("owner/repo", "123")
        assert tracker.is_duplicate("owner/repo", "456") is False

    def test_different_repos_not_duplicates(self) -> None:
        tracker = RunTracker(ttl_seconds=60)
        tracker.record("owner/repo1", "123")
        assert tracker.is_duplicate("owner/repo2", "123") is False

    def test_ttl_expiry(self) -> None:
        tracker = RunTracker(ttl_seconds=0)
        tracker.record("owner/repo", "123")
        time.sleep(0.01)
        assert tracker.is_duplicate("owner/repo", "123") is False

    def test_get_active_runs(self) -> None:
        tracker = RunTracker(ttl_seconds=60)
        tracker.record("owner/repo", "1", status="processing")
        tracker.record("owner/repo", "2", status="completed")
        active = tracker.get_active_runs()
        assert len(active) == 1
        assert active[0]["run_id"] == "1"

    def test_count_by_status(self) -> None:
        tracker = RunTracker(ttl_seconds=60)
        tracker.record("owner/repo", "1", status="processing")
        tracker.record("owner/repo", "2", status="completed")
        tracker.record("owner/repo", "3", status="error")
        counts = tracker.count_by_status()
        assert counts["processing"] == 1
        assert counts["completed"] == 1
        assert counts["error"] == 1

    def test_update_status(self) -> None:
        tracker = RunTracker(ttl_seconds=60)
        tracker.record("owner/repo", "123", status="processing")
        tracker.update_status("owner/repo", "123", "completed")
        runs = tracker.get_all_runs()
        assert runs[0]["status"] == "completed"

    def test_clear(self) -> None:
        tracker = RunTracker(ttl_seconds=60)
        tracker.record("owner/repo", "1")
        tracker.record("owner/repo", "2")
        tracker.clear()
        assert len(tracker.get_all_runs()) == 0

    def test_get_all_runs(self) -> None:
        tracker = RunTracker(ttl_seconds=60)
        tracker.record("owner/repo", "1")
        tracker.record("owner/repo", "2")
        assert len(tracker.get_all_runs()) == 2
