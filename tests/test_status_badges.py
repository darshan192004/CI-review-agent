from __future__ import annotations

from ui.badges import status_badge


def test_agent_working_badge_pulses() -> None:
    html = str(status_badge("AGENT_WORKING"))
    assert "badge-blue" in html
    assert "status-dot-pulse" in html
    assert "Agent Working" in html


def test_processing_badge_pulses() -> None:
    html = str(status_badge("processing"))
    assert "status-dot-pulse" in html
    assert "Processing" in html


def test_running_badge_pulses() -> None:
    assert "status-dot-pulse" in str(status_badge("RUNNING"))


def test_fix_pushed_badge_is_active_blue() -> None:
    html = str(status_badge("FIX_PUSHED"))
    assert "badge-blue" in html
    assert "status-dot-pulse" in html
    assert "Fix Pushed" in html


def test_passed_badge_is_stable_green() -> None:
    html = str(status_badge("PASSED"))
    assert "badge-green" in html
    assert "status-dot-pulse" not in html
    assert "Passed" in html


def test_success_badge_maps_to_passed() -> None:
    assert "Passed" in str(status_badge("success"))


def test_failed_badge_is_stable_red() -> None:
    html = str(status_badge("FAILED"))
    assert "badge-red" in html
    assert "status-dot-pulse" not in html
    assert "Failed" in html


def test_failed_lowercase_maps() -> None:
    assert "Failed" in str(status_badge("failed"))


def test_failure_variant_maps_to_failed_badge() -> None:
    html = str(status_badge("failure"))
    assert "badge-red" in html
    assert "status-dot-pulse" not in html
    assert "Failed" in html


def test_error_badge_is_amber() -> None:
    html = str(status_badge("error"))
    assert "badge-orange" in html
    assert "status-dot-pulse" not in html
    assert "Error" in html


def test_exhausted_badge_is_amber() -> None:
    html = str(status_badge("EXHAUSTED"))
    assert "badge-orange" in html
    assert "Exhausted" in html


def test_unknown_status_uses_purple_and_escapes() -> None:
    html = str(status_badge("QUEUED"))
    assert "badge-purple" in html
    assert "QUEUED" in html


def test_malicious_status_is_escaped() -> None:
    html = str(status_badge("<script>alert(1)</script>"))
    assert "<script>" not in html
