from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ui.formatters import format_failure_summary, format_run_time

_IST = ZoneInfo("Asia/Kolkata")


def test_format_run_time_empty() -> None:
    assert format_run_time(0) == "\u2014"
    assert format_run_time(None) == "\u2014"


def test_format_run_time_renders_absolute_ist_time() -> None:
    assert format_run_time(1_700_000_000.0) == "2023-11-15 03:43:20 IST"


def test_format_run_time_is_in_ist_not_utc() -> None:
    epoch = 1_700_000_000.0
    utc = datetime.fromtimestamp(epoch, tz=ZoneInfo("UTC"))
    rendered = format_run_time(epoch)
    assert rendered.endswith("IST")
    assert "2023-11-14 22:13:20" not in rendered
    assert utc.hour != int(rendered[11:13])


def test_format_run_time_future_epoch_renders_absolute_time() -> None:
    future = 1_800_000_000.0
    expected = datetime.fromtimestamp(future, tz=_IST).strftime("%Y-%m-%d %H:%M:%S") + " IST"
    assert format_run_time(future) == expected


def test_format_failure_summary_returns_stored_text_single_line() -> None:
    assert format_failure_summary("FAILED", "line one\nline two\t tabbed") == "line one line two tabbed"


def test_format_failure_summary_fallback_for_failed_runs() -> None:
    assert format_failure_summary("failure", "") == "Run failed \u2014 no details captured."
    assert format_failure_summary("FAILED", None) == "Run failed \u2014 no details captured."
    assert format_failure_summary("failed", "   ") == "Run failed \u2014 no details captured."


def test_format_failure_summary_empty_for_non_failures() -> None:
    assert format_failure_summary("PASSED", "") == ""
    assert format_failure_summary("error", "") == ""
    assert format_failure_summary("processing", None) == ""


def test_format_failure_summary_keeps_error_summary_when_present() -> None:
    assert format_failure_summary("error", "compile failed") == "compile failed"
