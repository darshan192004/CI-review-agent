from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ui.status import is_failure

_IST = ZoneInfo("Asia/Kolkata")


def format_run_time(epoch: float | None) -> str:
    """Format a wall-clock epoch as an absolute IST timestamp.

    Empty or falsy timestamps render an em-dash so legacy rows and events
    without a recorded time never show a misleading value.
    """
    if not epoch:
        return "\u2014"
    dt = datetime.fromtimestamp(float(epoch), tz=_IST)
    return f"{dt:%Y-%m-%d %H:%M:%S} {dt.tzname()}"


def format_failure_summary(status: str, summary: str | None) -> str:
    """Return a one-line failure summary for the runs table.

    Collapses newlines/whitespace in the stored summary. Failed runs with no
    captured summary get a reliable placeholder; other statuses return an
    empty string (the template renders an em-dash).
    """
    text = " ".join((summary or "").split())
    if text:
        return text
    if is_failure(status):
        return "Run failed \u2014 no details captured."
    return ""
