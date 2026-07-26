from __future__ import annotations

import time
import logging
from typing import Any

logger = logging.getLogger(__name__)


class RunTracker:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._ttl = ttl_seconds

    def _make_key(self, repository: str, run_id: str) -> str:
        return f"{repository}:{run_id}"

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [
            key
            for key, entry in self._runs.items()
            if now - entry["timestamp"] > self._ttl
        ]
        for key in expired:
            del self._runs[key]
        if expired:
            logger.debug("Evicted %d expired run entries", len(expired))

    def is_duplicate(self, repository: str, run_id: str) -> bool:
        self._evict_expired()
        key = self._make_key(repository, run_id)
        return key in self._runs

    def record(self, repository: str, run_id: str, status: str = "processing") -> None:
        key = self._make_key(repository, run_id)
        self._runs[key] = {
            "repository": repository,
            "run_id": run_id,
            "status": status,
            "timestamp": time.monotonic(),
        }
        logger.info("Recorded run: %s (status=%s)", key, status)

    def update_status(self, repository: str, run_id: str, status: str) -> None:
        key = self._make_key(repository, run_id)
        if key in self._runs:
            self._runs[key]["status"] = status
            self._runs[key]["timestamp"] = time.monotonic()

    def get_active_runs(self) -> list[dict[str, Any]]:
        self._evict_expired()
        return [
            entry for entry in self._runs.values() if entry["status"] == "processing"
        ]

    def get_all_runs(self) -> list[dict[str, Any]]:
        self._evict_expired()
        return list(self._runs.values())

    def count_by_status(self) -> dict[str, int]:
        self._evict_expired()
        counts: dict[str, int] = {}
        for entry in self._runs.values():
            status = entry["status"]
            counts[status] = counts.get(status, 0) + 1
        return counts

    def clear(self) -> None:
        self._runs.clear()


run_tracker = RunTracker()
