from __future__ import annotations

from services.messaging.channels.base import WebhookChannel
from services.messaging.formats import AlertPayload


class DiscordChannel(WebhookChannel):
    name = "discord"

    def build_body(self, payload: AlertPayload) -> dict[str, str]:
        return {"content": payload.incident_title + "\n" + payload.root_cause + "\n" + payload.resolution_steps}
