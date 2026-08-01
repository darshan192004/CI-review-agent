from __future__ import annotations

import pytest

from services.commit_message import (
    AUTOMATED_BY,
    MAX_SUBJECT_LENGTH,
    build_commit_message,
    derive_scope,
    derive_summary,
)


class TestDeriveScope:
    def test_top_level_directory_of_first_file(self) -> None:
        assert derive_scope(["services/commit_message.py", "ui/app.py"]) == "services"

    def test_single_segment_path_falls_back_to_ci(self) -> None:
        assert derive_scope(["setup.py"]) == "ci"

    def test_empty_paths_fall_back_to_ci(self) -> None:
        assert derive_scope([]) == "ci"

    def test_none_falls_back_to_ci(self) -> None:
        assert derive_scope(None) == "ci"


class TestDeriveSummary:
    def test_first_sentence_lowercased(self) -> None:
        assert derive_summary("Fix the flaky timeout in the test runner.") == "fix the flaky timeout in the test runner"

    def test_empty_explanation_has_fallback(self) -> None:
        assert derive_summary("") == "auto-repair ci failure"

    def test_whitespace_only_has_fallback(self) -> None:
        assert derive_summary("   ") == "auto-repair ci failure"


class TestBuildCommitMessage:
    def test_subject_format_with_attempt(self) -> None:
        msg = build_commit_message(
            summary="fix flaky timeout",
            scope="services",
            attempt=2,
        )
        assert msg.splitlines()[0] == "fix(services): fix flaky timeout (attempt 2)"

    def test_subject_format_without_attempt(self) -> None:
        msg = build_commit_message(summary="fix flaky timeout", scope="services")
        assert msg.splitlines()[0] == "fix(services): fix flaky timeout"

    def test_default_scope_when_not_provided(self) -> None:
        msg = build_commit_message(
            summary="fix flaky timeout",
            file_reasons=[("services/x.py", "resolved race")],
        )
        assert msg.splitlines()[0].startswith("fix(services):")

    def test_subject_truncated_to_max_length(self) -> None:
        long_summary = "a very long summary " * 10
        msg = build_commit_message(summary=long_summary, scope="services", attempt=3)
        first = msg.splitlines()[0]
        assert len(first) <= MAX_SUBJECT_LENGTH
        assert first.endswith("…")

    def test_contains_required_sections(self) -> None:
        msg = build_commit_message(
            summary="fix flaky timeout",
            scope="services",
            explanation="Root cause analysis text.",
            file_reasons=[("services/x.py", "fixed race")],
            repo="acme/app",
            run_id="123",
            attempt=1,
        )
        assert "Root Cause:\nRoot cause analysis text." in msg
        assert "Changes:\n- services/x.py: fixed race" in msg
        assert "CI-Run: acme/app#123" in msg
        assert "Fix-Attempt: 1" in msg
        assert f"Automated-By: {AUTOMATED_BY}" in msg

    def test_changes_lists_every_file_with_reason(self) -> None:
        msg = build_commit_message(
            summary="fix",
            scope="app",
            file_reasons=[
                ("services/a.py", "reason one"),
                ("services/b.py", "reason two"),
            ],
        )
        assert "- services/a.py: reason one" in msg
        assert "- services/b.py: reason two" in msg

    def test_empty_reason_has_fallback(self) -> None:
        msg = build_commit_message(
            summary="fix",
            scope="app",
            file_reasons=[("services/a.py", "")],
        )
        assert "- services/a.py: auto-fix applied" in msg

    def test_no_files_omits_changes_section(self) -> None:
        msg = build_commit_message(summary="fix", scope="app")
        assert "Changes:" not in msg

    def test_no_repo_omits_ci_run_line(self) -> None:
        msg = build_commit_message(summary="fix", scope="app")
        assert "CI-Run:" not in msg

    def test_no_attempt_omits_fix_attempt_line(self) -> None:
        msg = build_commit_message(summary="fix", scope="app")
        assert "Fix-Attempt:" not in msg

    def test_multiline_root_cause_preserved(self) -> None:
        msg = build_commit_message(
            summary="fix",
            scope="app",
            explanation="First line.\nSecond line with details.",
        )
        assert "Root Cause:\nFirst line.\nSecond line with details." in msg

    def test_raises_when_no_summary(self) -> None:
        with pytest.raises(ValueError):
            build_commit_message(summary="", scope="app")
