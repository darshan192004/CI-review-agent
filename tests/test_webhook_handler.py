from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.webhook_handler import handle_webhook_event
from services.webhook_models import CIPlatform, WebhookEvent, WebhookRepository, WebhookSender


def _make_event(
    author: str = "testuser",
    run_id: str = "42",
    run_attempt: str = "1",
    repo: str = "testorg/testrepo",
    branch: str = "main",
    sha: str = "abc123",
    status: str = "completed",
) -> WebhookEvent:
    return WebhookEvent(
        platform=CIPlatform.FORGEJO,
        action="completed",
        repository=WebhookRepository(full_name=repo, default_branch="main"),
        sender=WebhookSender(login=author),
        branch=branch,
        commit_sha=sha,
        run_id=run_id,
        run_attempt=run_attempt,
        status=status,
        author=author,
    )


class TestHandleWebhookEventBotFilter:
    @pytest.mark.asyncio
    @patch("services.webhook_handler.run_agent", new_callable=AsyncMock)
    @patch("server.broadcast_event")
    @patch("services.webhook_handler.run_tracker")
    @patch("services.webhook_handler.settings")
    async def test_skips_bot_author(
        self,
        mock_settings: object,
        mock_run_tracker: AsyncMock,
        mock_broadcast: object,
        mock_run_agent: AsyncMock,
    ) -> None:
        mock_settings.ci_bot_username = "ci-bot"
        mock_settings.max_retry_attempts = 3
        mock_settings.auto_fix_reruns = "true"
        mock_run_tracker.is_duplicate = AsyncMock(return_value=False)
        mock_run_tracker.record = AsyncMock()

        event = _make_event(author="ci-bot", run_id="99")
        await handle_webhook_event(event)

        mock_run_tracker.record.assert_called_once()
        call_kwargs = mock_run_tracker.record.call_args
        assert "skipped_bot" in str(call_kwargs)
        mock_run_agent.assert_not_called()

    @pytest.mark.asyncio
    @patch("services.webhook_handler.run_agent", new_callable=AsyncMock)
    @patch("server.broadcast_event")
    @patch("services.webhook_handler.run_tracker")
    @patch("services.webhook_handler.settings")
    async def test_passes_human_author(
        self,
        mock_settings: object,
        mock_run_tracker: AsyncMock,
        mock_broadcast: object,
        mock_run_agent: AsyncMock,
    ) -> None:
        mock_settings.ci_bot_username = "ci-bot"
        mock_settings.max_retry_attempts = 3
        mock_settings.auto_fix_reruns = "true"
        mock_run_tracker.is_duplicate = AsyncMock(return_value=False)
        mock_run_tracker.is_completed = AsyncMock(return_value=False)
        mock_run_tracker.record = AsyncMock()
        mock_run_tracker.update_status = AsyncMock()
        mock_run_agent.return_value = {"ci_status": "PASSED", "attempt_count": 0}

        event = _make_event(author="testadmin", run_id="99")
        await handle_webhook_event(event)

        mock_run_agent.assert_called_once()

    @pytest.mark.asyncio
    @patch("services.webhook_handler.run_agent", new_callable=AsyncMock)
    @patch("server.broadcast_event")
    @patch("services.webhook_handler.run_tracker")
    @patch("services.webhook_handler.settings")
    async def test_skips_when_bot_username_not_configured(
        self,
        mock_settings: object,
        mock_run_tracker: AsyncMock,
        mock_broadcast: object,
        mock_run_agent: AsyncMock,
    ) -> None:
        mock_settings.ci_bot_username = ""
        mock_settings.max_retry_attempts = 3
        mock_settings.auto_fix_reruns = "true"
        mock_run_tracker.is_duplicate = AsyncMock(return_value=False)
        mock_run_tracker.is_completed = AsyncMock(return_value=False)
        mock_run_tracker.record = AsyncMock()
        mock_run_tracker.update_status = AsyncMock()
        mock_run_agent.return_value = {"ci_status": "PASSED", "attempt_count": 0}

        event = _make_event(author="ci-bot", run_id="99")
        await handle_webhook_event(event)

        mock_run_agent.assert_called_once()

    @pytest.mark.asyncio
    @patch("services.webhook_handler.run_agent", new_callable=AsyncMock)
    @patch("server.broadcast_event")
    @patch("services.webhook_handler.run_tracker")
    @patch("services.webhook_handler.settings")
    async def test_skips_empty_author(
        self,
        mock_settings: object,
        mock_run_tracker: AsyncMock,
        mock_broadcast: object,
        mock_run_agent: AsyncMock,
    ) -> None:
        mock_settings.ci_bot_username = "ci-bot"
        mock_settings.max_retry_attempts = 3
        mock_settings.auto_fix_reruns = "true"
        mock_run_tracker.is_duplicate = AsyncMock(return_value=False)
        mock_run_tracker.is_completed = AsyncMock(return_value=False)
        mock_run_tracker.record = AsyncMock()
        mock_run_tracker.update_status = AsyncMock()
        mock_run_agent.return_value = {"ci_status": "PASSED", "attempt_count": 0}

        event = _make_event(author="", run_id="99")
        await handle_webhook_event(event)

        mock_run_agent.assert_called_once()
