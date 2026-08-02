from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

_ENV_PATH = Path(".env")
_EXAMPLE_PATH = Path(".env.example")

_PROMPTS: list[tuple[str, str, bool]] = [
    ("LLM_PROVIDER", "LLM provider (openai/anthropic)", True),
    ("", "", False),  # section break
    ("OPENAI_API_KEY", "OpenAI API key (sk-...)", False),
    ("OPENAI_MODEL", "OpenAI model", True),
    ("", "", False),
    ("ANTHROPIC_API_KEY", "Anthropic API key (sk-ant-...)", False),
    ("ANTHROPIC_MODEL", "Anthropic model", True),
    ("", "", False),
    ("GITHUB_TOKEN", "GitHub personal access token", False),
    ("FORGEJO_TOKEN", "Forgejo access token", False),
    ("FORGEJO_BASE_URL", "Forgejo base URL", True),
    ("", "", False),
    ("MESSAGING_PLATFORM", "Messaging platform (mattermost/slack/discord/telegram)", True),
    ("MATTERMOST_WEBHOOK_URL", "Mattermost webhook URL", False),
    ("SLACK_WEBHOOK_URL", "Slack webhook URL", False),
    ("DISCORD_WEBHOOK_URL", "Discord webhook URL", False),
    ("TELEGRAM_BOT_TOKEN", "Telegram bot token", False),
    ("TELEGRAM_CHAT_ID", "Telegram chat ID", False),
    ("", "", False),
    ("FORGEJO_WEBHOOK_SECRET", "Forgejo webhook secret", False),
    ("GITHUB_WEBHOOK_SECRET", "GitHub webhook secret", False),
    ("", "", False),
    ("GIT_REPO_PATH", "Git repository path", True),
]

_DEFAULTS: dict[str, str] = {
    "LLM_PROVIDER": "openai",
    "OPENAI_MODEL": "gpt-4o",
    "ANTHROPIC_MODEL": "claude-sonnet-4-20250514",
    "FORGEJO_BASE_URL": "https://forgejo.example.com",
    "MESSAGING_PLATFORM": "mattermost",
    "GIT_REPO_PATH": ".",
}


def _read_existing() -> dict[str, str]:
    env: dict[str, str] = {}
    if _ENV_PATH.exists():
        with open(_ENV_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.removeprefix("export").strip()
                    value = value.strip().strip("'\"")
                    env[key] = value
    return env


def _write_env(values: dict[str, str]) -> None:
    lines: list[str] = []
    if _EXAMPLE_PATH.exists():
        with open(_EXAMPLE_PATH, encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.rstrip("\n")
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.split("=", 1)[0].strip()
                    if key in values and values[key]:
                        lines.append(f"{key}={values[key]}")
                    else:
                        lines.append(line)
                else:
                    lines.append(line)
    else:
        for key, value in values.items():
            if value:
                lines.append(f"{key}={value}")

    _ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _is_secret(key: str) -> bool:
    return any(tag in key.upper() for tag in ("TOKEN", "KEY", "SECRET", "PASSWORD", "WEBHOOK_URL"))


def run_wizard() -> None:
    print("=" * 60)
    print("  CI Review Agent — First-Run Setup")
    print("=" * 60)
    print()

    if _ENV_PATH.exists():
        resp = input(f"  .env file already exists. Overwrite? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("  Keeping existing .env file.")
            return

    existing = _read_existing()
    values: dict[str, str] = {}

    for key, prompt_text, has_default in _PROMPTS:
        if not key:
            print()
            continue

        default = existing.get(key, _DEFAULTS.get(key, ""))
        default_display = "****" if default and _is_secret(key) else default

        if default:
            user_input = input(f"  {prompt_text} [{default_display}]: ").strip()
            if not user_input:
                values[key] = default
            else:
                values[key] = user_input
        else:
            if _is_secret(key):
                user_input = getpass.getpass(f"  {prompt_text}: ").strip()
            else:
                user_input = input(f"  {prompt_text}: ").strip()
            values[key] = user_input if user_input else default

    _write_env(values)

    filled = sum(1 for v in values.values() if v)
    total = len([k for k, _, _ in _PROMPTS if k])

    print()
    print("=" * 60)
    print(f"  Setup complete! {filled}/{total} variables configured.")
    print(f"  Config written to: {_ENV_PATH}")
    print()
    print("  Next steps:")
    print("    1. Start the agent:       ci-agent serve")
    print("    2. Open the dashboard:    http://127.0.0.1:8000")
    print("=" * 60)


if __name__ == "__main__":
    try:
        run_wizard()
    except KeyboardInterrupt:
        print("\n  Setup cancelled.")
        sys.exit(1)
