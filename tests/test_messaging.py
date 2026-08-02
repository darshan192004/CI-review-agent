from __future__ import annotations

import pytest

import services.messaging as messaging_mod
from config import Settings
from services.messaging import notification_allowed
from services.messaging.channels import get_channel
from services.messaging.formats import AlertPayload


def _payload(platform: str = "slack") -> AlertPayload:
    return AlertPayload(
        platform=platform,
        incident_title="CI Failed: acme/app",
        root_cause="Timeout waiting for the runner",
        resolution_steps="Step one\nStep two",
    )


class TestAlertPayload:
    def test_requires_platform_and_fields(self) -> None:
        p = _payload("mattermost")
        assert p.platform == "mattermost"
        assert p.incident_title
        assert p.root_cause
        assert p.resolution_steps


class TestChannelDispatch:
    @pytest.mark.parametrize("platform", ["mattermost", "slack", "discord", "telegram"])
    def test_get_channel_returns_expected(self, platform: str) -> None:
        channel = get_channel(platform)
        assert channel.name == platform

    def test_unknown_platform_raises(self) -> None:
        with pytest.raises(ValueError):
            get_channel("teams")


class TestChannelSend:
    @pytest.mark.asyncio
    async def test_slack_posts_webhook_payload(self) -> None:
        channel = get_channel("slack", webhook_url="https://hooks.slack.com/xxx")
        captured: dict = {}

        async def fake_post(url: str, body: dict) -> None:
            captured["url"] = url
            captured["body"] = body

        channel._post = fake_post  # type: ignore[method-assign]
        await channel.send(_payload("slack"))

        assert captured["url"] == "https://hooks.slack.com/xxx"
        assert "text" in captured["body"]

    @pytest.mark.asyncio
    async def test_discord_posts_webhook_payload(self) -> None:
        channel = get_channel("discord", webhook_url="https://discord.com/api/webhooks/1")
        captured: dict = {}

        async def fake_post(url: str, body: dict) -> None:
            captured["url"] = url
            captured["body"] = body

        channel._post = fake_post  # type: ignore[method-assign]
        await channel.send(_payload("discord"))

        assert captured["url"] == "https://discord.com/api/webhooks/1"
        assert "content" in captured["body"]

    @pytest.mark.asyncio
    async def test_telegram_posts_to_bot_endpoint(self) -> None:
        channel = get_channel("telegram", bot_token="tok123", chat_id="chat456")
        captured: dict = {}

        async def fake_post(url: str, body: dict) -> None:
            captured["url"] = url
            captured["body"] = body

        channel._post = fake_post  # type: ignore[method-assign]
        await channel.send(_payload("telegram"))

        assert captured["url"] == "https://api.telegram.org/bottok123/sendMessage"
        assert captured["body"]["chat_id"] == "chat456"
        assert "text" in captured["body"]

    def test_missing_webhook_url_raises(self) -> None:
        channel = get_channel("slack")
        with pytest.raises(ValueError):
            channel._endpoint()

    def test_missing_telegram_token_raises(self) -> None:
        channel = get_channel("telegram")
        with pytest.raises(ValueError):
            channel._endpoint()

    def test_missing_telegram_chat_id_raises(self) -> None:
        channel = get_channel("telegram", bot_token="tok123")
        with pytest.raises(ValueError):
            channel.build_body(_payload("telegram"))


class TestNotificationGating:
    @pytest.mark.parametrize("kind", ["failure", "success", "escalation"])
    def test_always_allows_everything(self, kind: str) -> None:
        assert notification_allowed("always", kind)

    @pytest.mark.parametrize("kind", ["failure", "success", "escalation"])
    def test_never_blocks_everything(self, kind: str) -> None:
        assert not notification_allowed("never", kind)

    def test_failures_only_allows_failure_and_escalation(self) -> None:
        assert notification_allowed("failures_only", "failure")
        assert notification_allowed("failures_only", "escalation")
        assert not notification_allowed("failures_only", "success")

    def test_success_only_allows_success(self) -> None:
        assert notification_allowed("success_only", "success")
        assert not notification_allowed("success_only", "failure")
        assert not notification_allowed("success_only", "escalation")

    def test_unknown_level_fails_open(self) -> None:
        assert notification_allowed("whenever", "failure")
        assert notification_allowed("whenever", "success")


class TestChannelConfig:
    def test_mattermost_maps_webhook(self, monkeypatch) -> None:
        monkeypatch.setattr(
            messaging_mod,
            "settings",
            Settings(_env_file=None, mattermost_webhook_url="https://mm.example.com/hooks/123"),
        )
        assert messaging_mod.channel_config("mattermost") == {"webhook_url": "https://mm.example.com/hooks/123"}

    def test_slack_maps_webhook(self, monkeypatch) -> None:
        monkeypatch.setattr(
            messaging_mod,
            "settings",
            Settings(_env_file=None, slack_webhook_url="https://hooks.slack.com/xxx"),
        )
        assert messaging_mod.channel_config("slack") == {"webhook_url": "https://hooks.slack.com/xxx"}

    def test_telegram_maps_token_and_chat(self, monkeypatch) -> None:
        monkeypatch.setattr(
            messaging_mod,
            "settings",
            Settings(_env_file=None, telegram_bot_token="tok123", telegram_chat_id="chat456"),
        )
        assert messaging_mod.channel_config("telegram") == {"bot_token": "tok123", "chat_id": "chat456"}

    def test_unknown_platform_raises(self, monkeypatch) -> None:
        monkeypatch.setattr(messaging_mod, "settings", Settings(_env_file=None))
        with pytest.raises(ValueError):
            messaging_mod.channel_config("teams")

    def test_is_channel_configured(self, monkeypatch) -> None:
        monkeypatch.setattr(
            messaging_mod,
            "settings",
            Settings(_env_file=None, mattermost_webhook_url="https://mm.example.com/hooks/123"),
        )
        assert messaging_mod.is_channel_configured("mattermost")
        assert not messaging_mod.is_channel_configured("slack")
        assert not messaging_mod.is_channel_configured("telegram")


class TestSendAlert:
    @pytest.mark.asyncio
    async def test_send_alert_dispatches_to_channel(self, monkeypatch) -> None:
        sent: list[AlertPayload] = []

        class FakeChannel:
            name = "slack"

            def __init__(self, **kwargs: str) -> None:
                self.kwargs = kwargs

            async def send(self, payload: AlertPayload) -> None:
                sent.append(payload)

        monkeypatch.setattr(messaging_mod, "get_channel", lambda name, **kw: FakeChannel(**kw))
        monkeypatch.setattr(messaging_mod, "settings", Settings(_env_file=None, messaging_platform="slack"))
        result = await messaging_mod.send_alert("slack", "CI Failed: acme/app", "timeout", "retry")
        assert result == "Alert sent via slack"
        assert sent and sent[0].platform == "slack"

    @pytest.mark.asyncio
    async def test_send_alert_unknown_platform_raises(self, monkeypatch) -> None:
        monkeypatch.setattr(messaging_mod, "settings", Settings(_env_file=None))
        with pytest.raises(ValueError):
            await messaging_mod.send_alert("teams", "t", "c", "r")

    @pytest.mark.asyncio
    async def test_send_alert_unconfigured_channel_raises(self, monkeypatch) -> None:
        monkeypatch.setattr(messaging_mod, "settings", Settings(_env_file=None))
        with pytest.raises(ValueError):
            await messaging_mod.send_alert("slack", "t", "c", "r")
