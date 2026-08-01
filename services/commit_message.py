from __future__ import annotations

import re

AUTOMATED_BY = "CI Review Agent"
DEFAULT_SCOPE = "ci"
DEFAULT_SUMMARY = "auto-repair ci failure"
MAX_SUBJECT_LENGTH = 72


def derive_scope(file_paths: list[str] | None = None) -> str:
    """Derive a git commit scope from the top-level directory of the first file."""
    for path in file_paths or []:
        parts = path.split("/")
        if len(parts) > 1 and parts[0]:
            return parts[0]
    return DEFAULT_SCOPE


def derive_summary(explanation: str) -> str:
    """Derive a short imperative action from the first sentence of an explanation."""
    text = " ".join((explanation or "").split())
    if not text:
        return DEFAULT_SUMMARY
    first_sentence = re.split(r"(?<=[.!?])\s+", text)[0].strip(" .!?")
    if not first_sentence:
        return DEFAULT_SUMMARY
    summary = first_sentence[:1].lower() + first_sentence[1:]
    return summary or DEFAULT_SUMMARY


def _truncate_subject(subject: str, max_length: int = MAX_SUBJECT_LENGTH) -> str:
    if len(subject) <= max_length:
        return subject
    return subject[: max_length - 1] + "…"


def build_commit_message(
    *,
    summary: str,
    scope: str | None = None,
    explanation: str = "",
    file_reasons: list[tuple[str, str]] | None = None,
    repo: str = "",
    run_id: str = "",
    attempt: int | None = None,
) -> str:
    """Assemble the standardized CI Review Agent commit message.

    Format:

        fix(<scope>): <short action> (attempt <N>)

        Root Cause:
        <explanation>

        Changes:
        - <file_path>: <reason>

        CI-Run: <repo>#<run_id>
        Fix-Attempt: <attempt>
        Automated-By: CI Review Agent

    The subject line is truncated to 72 characters. The scope falls back to the
    top-level directory of the first changed file, then to "ci".
    """
    summary = (summary or "").strip()
    if not summary:
        raise ValueError("commit message requires a summary")

    effective_scope = scope or derive_scope([path for path, _ in file_reasons or []])
    subject = f"fix({effective_scope}): {summary}"
    if attempt is not None:
        subject += f" (attempt {attempt})"
    subject = _truncate_subject(subject)

    root_cause = (explanation or "").strip() or "CI failure auto-repaired by the CI Review Agent."

    lines = [subject, "", "Root Cause:", root_cause]
    if file_reasons:
        lines.append("")
        lines.append("Changes:")
        for path, reason in file_reasons:
            lines.append(f"- {path}: {reason or 'auto-fix applied'}")
    if repo or run_id:
        lines.append("")
        lines.append(f"CI-Run: {repo}#{run_id}")
    if attempt is not None:
        lines.append(f"Fix-Attempt: {attempt}")
    lines.append(f"Automated-By: {AUTOMATED_BY}")
    return "\n".join(lines)
