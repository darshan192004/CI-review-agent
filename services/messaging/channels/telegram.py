from __future__ import annotations

import re

from services.messaging.channels.base import BaseChannel
from services.messaging.formats import AlertPayload

_BOT_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_\-]{20,}$")
_CHAT_ID_RE = re.compile(r"^-?\d+$")


class TelegramChannel(BaseChannel):
    name = "telegram"

    def __init__(self, bot_token: str = "", chat_id: str = "") -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    def _endpoint(self) -> str:
        if not self.bot_token:
            raise ValueError("telegram bot token not configured")
        if not _BOT_TOKEN_RE.fullmatch(self.bot_token):
            raise ValueError(
                "telegram bot token is invalid: expected the format <bot_id>:<auth_token> "
                "(e.g. 123456789:AA...), as shown by @BotFather"
            )
        return f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    def build_body(self, payload: AlertPayload) -> dict[str, str]:
        if not self.chat_id:
            raise ValueError("telegram chat id not configured")
        if not _CHAT_ID_RE.fullmatch(self.chat_id):
            raise ValueError("telegram chat id must be numeric (e.g. 123456789 or -1001234567890)")
        return {
            "chat_id": self.chat_id,
            "text": payload.incident_title + "\n" + payload.root_cause + "\n" + payload.resolution_steps,
        }
