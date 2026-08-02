from __future__ import annotations

from services.messaging.channels.base import BaseChannel
from services.messaging.formats import AlertPayload


class TelegramChannel(BaseChannel):
    name = "telegram"

    def __init__(self, bot_token: str = "", chat_id: str = "") -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    def _endpoint(self) -> str:
        if not self.bot_token:
            raise ValueError("telegram bot token not configured")
        return f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    def build_body(self, payload: AlertPayload) -> dict[str, str]:
        if not self.chat_id:
            raise ValueError("telegram chat id not configured")
        return {
            "chat_id": self.chat_id,
            "text": payload.incident_title + "\n" + payload.root_cause + "\n" + payload.resolution_steps,
        }
