from __future__ import annotations

import re

_ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
_ERROR_PATTERNS = re.compile(
    r"(Traceback|ERROR|FAIL|Panic|Exception|assert|panic|FATAL|error:|Error:|FAILED)",
    re.IGNORECASE,
)
_CONTEXT_LINES = 5
_MAX_CHARS = 16000


def strip_ansi(text: str) -> str:
    return _ANSI_PATTERN.sub("", text)


def parse_ci_logs(raw_logs: str, max_chars: int = _MAX_CHARS) -> str:
    cleaned = strip_ansi(raw_logs)
    lines = cleaned.splitlines()

    hit_lines: set[int] = set()
    for i, line in enumerate(lines):
        if _ERROR_PATTERNS.search(line):
            start = max(0, i - _CONTEXT_LINES)
            end = min(len(lines), i + _CONTEXT_LINES + 1)
            for j in range(start, end):
                hit_lines.add(j)

    if not hit_lines:
        trimmed = cleaned[:max_chars]
        if len(cleaned) > max_chars:
            trimmed += "\n\n... [truncated]"
        return trimmed

    sorted_hits = sorted(hit_lines)
    sections: list[str] = []
    prev = -2
    current_section: list[str] = []

    for idx in sorted_hits:
        if idx > prev + 1 and current_section:
            sections.append("\n".join(current_section))
            current_section = []
        current_section.append(
            f"{'>>>' if _ERROR_PATTERNS.search(lines[idx]) else '   '} L{idx + 1}: {lines[idx]}"
        )
        prev = idx

    if current_section:
        sections.append("\n".join(current_section))

    result = "\n---\n".join(sections)

    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n... [truncated]"

    return result
