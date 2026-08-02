from __future__ import annotations

from services.messaging.channels.base import BaseChannel
from services.messaging.channels.discord import DiscordChannel
from services.messaging.channels.mattermost import MattermostChannel
from services.messaging.channels.slack import SlackChannel
from services.messaging.channels.telegram import TelegramChannel

CHANNELS: dict[str, type[BaseChannel]] = {
    channel.name: channel for channel in (DiscordChannel, MattermostChannel, SlackChannel, TelegramChannel)
}


def get_channel(name: str, **config: str) -> BaseChannel:
    try:
        channel_cls = CHANNELS[name]
    except KeyError:
        raise ValueError(f"Unsupported channel: {name}") from None
    return channel_cls(**config)


__all__ = ["BaseChannel", "CHANNELS", "get_channel"]
