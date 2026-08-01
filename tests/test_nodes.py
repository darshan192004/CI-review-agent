from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from nodes import node_llm_fix_code


class _FakeLLMResponse:
    def __init__(self, content: str) -> None:
        self.content = content


def _make_state(**overrides) -> dict:
    state = {
        "repository": "testorg/testrepo",
        "branch": "main",
        "commit_sha": "abc123",
        "ci_platform": "forgejo",
        "run_id": "42",
        "run_attempt": "1",
        "attempt_count": 1,
        "ci_status": "RUNNING",
        "failed_logs": ("tests/test_calc.py::test_subtract FAILED\nassert subtract(5, 2) == 3\nAssertionError"),
        "llm_analysis": "",
        "explanation": "",
        "patch_applied": False,
        "repo_info": {
            "name": "testorg/testrepo",
            "clone_url": "http://localhost:3000/testorg/testrepo.git",
            "token": "tok",
            "branch": "main",
            "commit_sha": "abc123",
        },
        "notifications_sent": [],
        "source_files": {"calc.py": "def subtract(a, b):\n    return b - a\n"},
        "failure_summary": "test_subtract FAILED",
    }
    state.update(overrides)
    return state


class TestNodeLlmFixCodeNoModifications:
    @pytest.mark.asyncio
    async def test_no_modifications_is_not_reported_as_passed(self, monkeypatch):
        # Regression: when the LLM proposes no code changes while CI is failing,
        # the run must NOT be marked PASSED (that would claim a green build that
        # never happened). It must be treated as an unresolved failure.
        fake_llm = AsyncMock()
        fake_llm.ainvoke.return_value = _FakeLLMResponse(
            '{"explanation": "no code change needed", "modified_files": []}'
        )
        monkeypatch.setattr("nodes._get_llm", lambda: fake_llm)

        result = await node_llm_fix_code(_make_state())

        assert result["ci_status"] == "CANNOT_FIX"
        assert result["patch_applied"] is False
        assert result["attempt_count"] == 2

    @pytest.mark.asyncio
    async def test_no_modifications_still_runs_through_infra_gate(self, monkeypatch):
        # Even with an LLM that would suggest changes, a runner/infra failure must
        # be escalated deterministically without invoking the LLM.
        fake_llm = AsyncMock()
        monkeypatch.setattr("nodes._get_llm", lambda: fake_llm)

        state = _make_state(
            failed_logs=(
                "❌  Failure - Main actions/checkout@v4\n"
                "⚙️ [runner]: copyDir: failed to copy content to container: "
                "path escapes from parent"
            )
        )
        result = await node_llm_fix_code(state)

        assert result["ci_status"] == "CANNOT_FIX"
        assert fake_llm.ainvoke.await_count == 0

    @pytest.mark.asyncio
    async def test_llm_failure_produces_explanation_and_escalates(self, monkeypatch):
        fake_llm = AsyncMock()
        fake_llm.ainvoke.side_effect = RuntimeError("boom")
        monkeypatch.setattr("nodes._get_llm", lambda: fake_llm)

        result = await node_llm_fix_code(_make_state())

        assert result["ci_status"] == "CANNOT_FIX"
        assert "boom" in result["explanation"]
