from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_ENV_PATH = Path(".env")

_SENSITIVE_KEYS = {
    "github_token",
    "forgejo_token",
    "openai_api_key",
    "anthropic_api_key",
    "forgejo_webhook_secret",
    "github_webhook_secret",
    "mattermost_webhook_url",
    "slack_webhook_url",
    "discord_webhook_url",
}


def _is_sensitive(key: str) -> bool:
    return key.lower() in _SENSITIVE_KEYS


def read_env(path: Path = _ENV_PATH) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            match = re.match(r"^(export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
            if match:
                key = match.group(2)
                value = match.group(3).strip().strip("'\"")
                env[key] = value
    return env


def read_env_redacted(path: Path = _ENV_PATH) -> dict[str, str]:
    env = read_env(path)
    redacted: dict[str, str] = {}
    for key, value in env.items():
        if _is_sensitive(key):
            redacted[key] = "••••••••" if value else ""
        else:
            redacted[key] = value
    return redacted


def write_env(updates: dict[str, str], path: Path = _ENV_PATH) -> None:
    existing: dict[str, str] = {}
    comments: list[str] = []
    lines: list[str] = []

    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                stripped = line.rstrip("\n")
                if stripped.startswith("#"):
                    comments.append(stripped)
                    lines.append(stripped)
                    continue
                match = re.match(
                    r"^(export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)", stripped
                )
                if match:
                    key = match.group(2)
                    existing[key] = stripped
                    lines.append(stripped)
                else:
                    lines.append(stripped)

    for key, value in updates.items():
        if key in existing:
            new_line = _rewrite_line(existing[key], value)
            for i, line in enumerate(lines):
                if line.rstrip() == existing[key]:
                    lines[i] = new_line
                    break
            existing.pop(key)
        else:
            lines.append(f"{key}={value}")

    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".env.tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
        os.replace(tmp_path, path)
        logger.info("Updated .env file with %d keys", len(updates))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _rewrite_line(original_line: str, new_value: str) -> str:
    match = re.match(r"^(export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)", original_line)
    if not match:
        return f"{new_value}"
    prefix = match.group(1) or ""
    key = match.group(2)
    needs_quote = " " in new_value or any(c in new_value for c in "'\"")
    if needs_quote:
        escaped = new_value.replace("\\", "\\\\").replace('"', '\\"')
        return f'{prefix}{key}="{escaped}"'
    return f"{prefix}{key}={new_value}"


def mask_value(value: str) -> str:
    if not value or len(value) <= 8:
        return "••••" if value else ""
    return value[:3] + "•" * (len(value) - 6) + value[-3:]
