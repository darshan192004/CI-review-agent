from __future__ import annotations

from ui.status import FAILED_VARIANTS, PASSED_VARIANTS, canonical_status, is_failure


def test_failed_variants_include_all_aliases() -> None:
    assert "failure" in FAILED_VARIANTS
    assert "failed" in FAILED_VARIANTS
    assert "FAILED" in FAILED_VARIANTS


def test_passed_variants_include_all_aliases() -> None:
    assert "PASSED" in PASSED_VARIANTS
    assert "success" in PASSED_VARIANTS


def test_canonical_status_unifies_failure_variants() -> None:
    assert canonical_status("failure") == "failed"
    assert canonical_status("failed") == "failed"
    assert canonical_status("FAILED") == "failed"


def test_canonical_status_unifies_passed_variants() -> None:
    assert canonical_status("PASSED") == "passed"
    assert canonical_status("success") == "passed"


def test_canonical_status_keeps_terminal_statuses_distinct() -> None:
    assert canonical_status("error") == "error"
    assert canonical_status("EXHAUSTED") == "exhausted"


def test_canonical_status_lowercases_other_statuses() -> None:
    assert canonical_status("AGENT_WORKING") == "agent_working"
    assert canonical_status("processing") == "processing"
    assert canonical_status("QUEUED") == "queued"


def test_canonical_status_round_trips_filter_values() -> None:
    assert canonical_status("failed") == canonical_status("failure")
    assert canonical_status("passed") == canonical_status("PASSED")
    assert canonical_status("agent_working") == canonical_status("AGENT_WORKING")


def test_is_failure_positive() -> None:
    assert is_failure("failure")
    assert is_failure("failed")
    assert is_failure("FAILED")


def test_is_failure_negative() -> None:
    assert not is_failure("error")
    assert not is_failure("EXHAUSTED")
    assert not is_failure("PASSED")
    assert not is_failure("processing")
