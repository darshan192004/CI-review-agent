from __future__ import annotations

import html as html_mod

from markupsafe import Markup

_ACTIVE_STATUSES = frozenset({"AGENT_WORKING", "processing", "RUNNING"})
_PASSED_STATUSES = frozenset({"PASSED", "success"})
_FAILED_STATUSES = frozenset({"FAILED", "failed"})
_ERROR_STATUSES = frozenset({"error", "EXHAUSTED"})

_ACTIVE_LABELS = {
    "AGENT_WORKING": "Agent Working",
    "processing": "Processing",
    "RUNNING": "Running",
}


def status_badge(status: str) -> Markup:
    """Single source of truth for run-status badges.

    Active/working states pulse; terminal states (passed/failed/error/exhausted)
    use stable dots. Shared by Jinja templates and the SSE renderer so the
    dashboard, runs page, and live updates stay visually identical.
    """
    if status in _ACTIVE_STATUSES:
        label = _ACTIVE_LABELS.get(status, "Processing")
        return Markup(
            '<span class="badge badge-blue">'
            '<span class="status-dot status-dot-pulse bg-blue-400"></span>'
            f"{label}</span>"
        )
    if status in _PASSED_STATUSES:
        return Markup('<span class="badge badge-green"><span class="status-dot bg-emerald-400"></span>Passed</span>')
    if status in _FAILED_STATUSES:
        return Markup('<span class="badge badge-red"><span class="status-dot bg-rose-400"></span>Failed</span>')
    if status in _ERROR_STATUSES:
        label = "Error" if status == "error" else "Exhausted"
        return Markup(f'<span class="badge badge-orange"><span class="status-dot bg-amber-400"></span>{label}</span>')
    return Markup(f'<span class="badge badge-purple">{html_mod.escape(status)}</span>')
