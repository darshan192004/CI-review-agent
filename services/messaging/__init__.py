from __future__ import annotations

import logging

from config import settings
from services.messaging.channels import CHANNELS, get_channel
from services.messaging.formats import AlertPayload

logger = logging.getLogger(__name__)


def notification_allowed(trigger_level: str, kind: str) -> bool:
    if trigger_level == "always":
        return True
    if trigger_level == "never":
        return False
    if trigger_level == "failures_only":
        return kind in ("failure", "escalation")
    if trigger_level == "success_only":
        return kind == "success"
    logger.warning("Unknown notification trigger level %r; notifications allowed", trigger_level)
    return True


def channel_config(platform: str) -> dict[str, str]:
    configs = {
        "mattermost": {"webhook_url": settings.mattermost_webhook_url},
        "slack": {"webhook_url": settings.slack_webhook_url},
        "discord": {"webhook_url": settings.discord_webhook_url},
        "telegram": {"bot_token": settings.telegram_bot_token, "chat_id": settings.telegram_chat_id},
    }
    try:
        return dict(configs[platform])
    except KeyError:
        raise ValueError(f"Unsupported channel: {platform}") from None


def is_channel_configured(platform: str) -> bool:
    return all(bool(value) for value in channel_config(platform).values())


async def send_alert(
    platform: str,
    incident_title: str,
    root_cause: str,
    resolution_steps: str,
) -> str:
    payload = AlertPayload(
        platform=platform,
        incident_title=incident_title,
        root_cause=root_cause,
        resolution_steps=resolution_steps,
    )
    channel = get_channel(payload.platform, **channel_config(payload.platform))
    await channel.send(payload)
    return f"Alert sent via {payload.platform}"


__all__ = [
    "AlertPayload",
    "CHANNELS",
    "channel_config",
    "get_channel",
    "is_channel_configured",
    "notification_allowed",
    "send_alert",
]
