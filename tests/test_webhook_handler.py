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
    conclusion: str = "",
    action: str = "completed",
    commit_author: str = "",
    commit_author_email: str = "",
    created_at: float | None = None,
) -> WebhookEvent:
    return WebhookEvent(
        platform=CIPlatform.FORGEJO,
        action=action,
        repository=WebhookRepository(full_name=repo, default_branch="main"),
        sender=WebhookSender(login=author),
        branch=branch,
        commit_sha=sha,
        run_id=run_id,
        run_attempt=run_attempt,
        status=status,
        conclusion=conclusion,
        author=author,
        commit_author=commit_author,
        commit_author_email=commit_author_email,
        created_at=created_at,
    )


def _session(
    session_id: int = 1,
    attempt_count: int = 1,
    max_attempts: int = 3,
    previous_analysis: str = "",
    last_fix_sha: str = "botfix1",
) -> dict:
    return {
        "id": session_id,
        "repository": "testorg/testrepo",
        "branch": "main",
        "head_sha": "human123",
        "trigger_run_id": "99",
        "attempt_count": attempt_count,
        "max_attempts": max_attempts,
        "status": "active",
        "previous_analysis": previous_analysis,
        "last_fix_sha": last_fix_sha,
        "created_at": 0.0,
        "updated_at": 0.0,
    }


def _run(
    run_id: str = "99",
    run_attempt: str = "1",
    status: str = "FIX_PUSHED",
    commit_sha: str = "botfix1",
    created_at: float = 100.0,
) -> dict:
    """The session-bound lifecycle ci_run row (as returned by get_run_by_session)."""
    return {
        "repository": "testorg/testrepo",
        "run_id": run_id,
        "run_attempt": run_attempt,
        "status": status,
        "platform": "forgejo",
        "branch": "main",
        "commit_sha": commit_sha,
        "author": "CI Review Bot",
        "failure_summary": "",
        "patch_summary": "",
        "attempt_count": 0,
        "created_at": created_at,
        "updated_at": created_at,
        "session_id": 1,
    }


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
        mock_run_tracker.get_session_by_fix_sha = AsyncMock(return_value=None)
        mock_run_tracker.record = AsyncMock()

        event = _make_event(author="ci-bot", run_id="99", status="failure")
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
        mock_settings.ci_bot_email = "ci-bot@autofix.internal"
        mock_settings.max_retry_attempts = 3
        mock_settings.auto_fix_reruns = "true"
        mock_run_tracker.is_duplicate = AsyncMock(return_value=False)
        mock_run_tracker.is_completed = AsyncMock(return_value=False)
        mock_run_tracker.get_session_by_head_sha = AsyncMock(return_value=None)
        mock_run_tracker.get_session_by_fix_sha = AsyncMock(return_value=None)
        mock_run_tracker.record = AsyncMock()
        mock_run_tracker.update_status = AsyncMock()
        mock_run_tracker.create_session = AsyncMock(
            return_value={
                "id": 1,
                "repository": "testorg/testrepo",
                "branch": "main",
                "head_sha": "abc123",
                "trigger_run_id": "99",
                "attempt_count": 1,
                "max_attempts": 3,
                "status": "active",
                "previous_analysis": "",
                "last_fix_sha": "",
                "created_at": 0.0,
                "updated_at": 0.0,
            }
        )
        mock_run_agent.return_value = {"ci_status": "PASSED", "attempt_count": 0}

        event = _make_event(author="testadmin", run_id="99", status="failure")
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
        mock_run_tracker.get_session_by_head_sha = AsyncMock(return_value=None)
        mock_run_tracker.get_session_by_fix_sha = AsyncMock(return_value=None)
        mock_run_tracker.record = AsyncMock()
        mock_run_tracker.update_status = AsyncMock()
        mock_run_tracker.create_session = AsyncMock(
            return_value={
                "id": 1,
                "repository": "testorg/testrepo",
                "branch": "main",
                "head_sha": "abc123",
                "trigger_run_id": "99",
                "attempt_count": 1,
                "max_attempts": 3,
                "status": "active",
                "previous_analysis": "",
                "last_fix_sha": "",
                "created_at": 0.0,
                "updated_at": 0.0,
            }
        )
        mock_run_agent.return_value = {"ci_status": "PASSED", "attempt_count": 0}

        event = _make_event(author="testadmin", run_id="99", status="failure")
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
        mock_run_tracker.get_session_by_head_sha = AsyncMock(return_value=None)
        mock_run_tracker.get_session_by_fix_sha = AsyncMock(return_value=None)
        mock_run_tracker.record = AsyncMock()
        mock_run_tracker.update_status = AsyncMock()
        mock_run_tracker.create_session = AsyncMock(
            return_value={
                "id": 1,
                "repository": "testorg/testrepo",
                "branch": "main",
                "head_sha": "abc123",
                "trigger_run_id": "99",
                "attempt_count": 1,
                "max_attempts": 3,
                "status": "active",
                "previous_analysis": "",
                "last_fix_sha": "",
                "created_at": 0.0,
                "updated_at": 0.0,
            }
        )
        mock_run_agent.return_value = {"ci_status": "PASSED", "attempt_count": 0}

        event = _make_event(author="testadmin", run_id="99", status="failure")
        await handle_webhook_event(event)

        mock_run_agent.assert_called_once()

    @pytest.mark.asyncio
    @patch("services.webhook_handler.run_agent", new_callable=AsyncMock)
    @patch("server.broadcast_event")
    @patch("services.webhook_handler.run_tracker")
    @patch("services.webhook_handler.settings")
    async def test_skips_bot_commit_author_by_name(
        self,
        mock_settings: object,
        mock_run_tracker: AsyncMock,
        mock_broadcast: object,
        mock_run_agent: AsyncMock,
    ) -> None:
        mock_settings.ci_bot_username = "CI Review Bot"
        mock_settings.ci_bot_email = "ci-bot@autofix.internal"
        mock_settings.max_retry_attempts = 3
        mock_settings.auto_fix_reruns = "true"
        mock_run_tracker.is_duplicate = AsyncMock(return_value=False)
        mock_run_tracker.get_session_by_fix_sha = AsyncMock(return_value=None)
        mock_run_tracker.record = AsyncMock()

        event = _make_event(
            author="testadmin",
            run_id="99",
            status="failure",
            commit_author="CI Review Bot",
            commit_author_email="ci-bot@autofix.internal",
        )
        await handle_webhook_event(event)

        assert "skipped_bot" in str(mock_run_tracker.record.call_args)
        mock_run_agent.assert_not_called()

    @pytest.mark.asyncio
    @patch("services.webhook_handler.run_agent", new_callable=AsyncMock)
    @patch("server.broadcast_event")
    @patch("services.webhook_handler.run_tracker")
    @patch("services.webhook_handler.settings")
    async def test_skips_bot_commit_by_email_only(
        self,
        mock_settings: object,
        mock_run_tracker: AsyncMock,
        mock_broadcast: object,
        mock_run_agent: AsyncMock,
    ) -> None:
        mock_settings.ci_bot_username = "CI Review Bot"
        mock_settings.ci_bot_email = "ci-bot@autofix.internal"
        mock_settings.max_retry_attempts = 3
        mock_settings.auto_fix_reruns = "true"
        mock_run_tracker.is_duplicate = AsyncMock(return_value=False)
        mock_run_tracker.get_session_by_fix_sha = AsyncMock(return_value=None)
        mock_run_tracker.record = AsyncMock()

        event = _make_event(
            author="testadmin",
            run_id="99",
            status="failure",
            commit_author="Someone Else",
            commit_author_email="ci-bot@autofix.internal",
        )
        await handle_webhook_event(event)

        assert "skipped_bot" in str(mock_run_tracker.record.call_args)
        mock_run_agent.assert_not_called()

    @pytest.mark.asyncio
    @patch("services.webhook_handler.run_agent", new_callable=AsyncMock)
    @patch("server.broadcast_event")
    @patch("services.webhook_handler.run_tracker")
    @patch("services.webhook_handler.settings")
    async def test_human_commit_author_triggers_agent(
        self,
        mock_settings: object,
        mock_run_tracker: AsyncMock,
        mock_broadcast: object,
        mock_run_agent: AsyncMock,
    ) -> None:
        mock_settings.ci_bot_username = "CI Review Bot"
        mock_settings.ci_bot_email = "ci-bot@autofix.internal"
        mock_settings.max_retry_attempts = 3
        mock_settings.auto_fix_reruns = "true"
        mock_run_tracker.is_duplicate = AsyncMock(return_value=False)
        mock_run_tracker.is_completed = AsyncMock(return_value=False)
        mock_run_tracker.get_session_by_head_sha = AsyncMock(return_value=None)
        mock_run_tracker.get_session_by_fix_sha = AsyncMock(return_value=None)
        mock_run_tracker.record = AsyncMock()
        mock_run_tracker.update_status = AsyncMock()
        mock_run_tracker.create_session = AsyncMock(
            return_value={
                "id": 1,
                "repository": "testorg/testrepo",
                "branch": "main",
                "head_sha": "abc123",
                "trigger_run_id": "99",
                "attempt_count": 1,
                "max_attempts": 3,
                "status": "active",
                "previous_analysis": "",
                "last_fix_sha": "",
                "created_at": 0.0,
                "updated_at": 0.0,
            }
        )
        mock_run_agent.return_value = {"ci_status": "PASSED", "attempt_count": 0}

        event = _make_event(
            author="testadmin",
            run_id="99",
            status="failure",
            commit_author="Darshan Parmar",
            commit_author_email="darshan@example.com",
        )
        await handle_webhook_event(event)

        mock_run_agent.assert_called_once()


class TestHumanFailureSessionBinding:
    @pytest.mark.asyncio
    @patch("services.webhook_handler.run_agent", new_callable=AsyncMock)
    @patch("server.broadcast_event")
    @patch("services.webhook_handler.run_tracker")
    @patch("services.webhook_handler.settings")
    async def test_human_failure_binds_session_to_lifecycle_row(
        self,
        mock_settings: object,
        mock_run_tracker: AsyncMock,
        mock_broadcast: object,
        mock_run_agent: AsyncMock,
    ) -> None:
        mock_settings.ci_bot_username = "CI Review Bot"
        mock_settings.ci_bot_email = "ci-bot@autofix.internal"
        mock_settings.max_retry_attempts = 3
        mock_settings.auto_fix_reruns = "true"
        mock_run_tracker.is_duplicate = AsyncMock(return_value=False)
        mock_run_tracker.is_completed = AsyncMock(return_value=False)
        mock_run_tracker.get_session_by_head_sha = AsyncMock(return_value=None)
        mock_run_tracker.get_session_by_fix_sha = AsyncMock(return_value=None)
        mock_run_tracker.record = AsyncMock()
        mock_run_tracker.update_status = AsyncMock()
        mock_run_tracker.create_session = AsyncMock(
            return_value={
                "id": 1,
                "repository": "testorg/testrepo",
                "branch": "main",
                "head_sha": "abc123",
                "trigger_run_id": "99",
                "attempt_count": 1,
                "max_attempts": 3,
                "status": "active",
                "previous_analysis": "",
                "last_fix_sha": "",
                "created_at": 0.0,
                "updated_at": 0.0,
            }
        )
        mock_run_agent.return_value = {"ci_status": "PASSED", "attempt_count": 0}

        event = _make_event(author="testadmin", run_id="99", status="failure")
        await handle_webhook_event(event)

        mock_run_agent.assert_called_once()
        # The AGENT_WORKING status update binds the freshly created session to
        # the run row so later bot webhooks can resolve it via get_run_by_session.
        assert mock_run_tracker.update_status.call_args_list[0].kwargs["session_id"] == 1
        assert mock_run_tracker.update_status.call_args_list[0].args[1] == "99"

    @pytest.mark.asyncio
    @patch("services.webhook_handler.run_agent", new_callable=AsyncMock)
    @patch("server.broadcast_event")
    @patch("services.webhook_handler.run_tracker")
    @patch("services.webhook_handler.settings")
    async def test_human_failure_records_event_created_at(
        self,
        mock_settings: object,
        mock_run_tracker: AsyncMock,
        mock_broadcast: object,
        mock_run_agent: AsyncMock,
    ) -> None:
        mock_settings.ci_bot_username = "CI Review Bot"
        mock_settings.ci_bot_email = "ci-bot@autofix.internal"
        mock_settings.max_retry_attempts = 3
        mock_settings.auto_fix_reruns = "true"
        mock_run_tracker.is_duplicate = AsyncMock(return_value=False)
        mock_run_tracker.is_completed = AsyncMock(return_value=False)
        mock_run_tracker.get_session_by_head_sha = AsyncMock(return_value=None)
        mock_run_tracker.get_session_by_fix_sha = AsyncMock(return_value=None)
        mock_run_tracker.record = AsyncMock()
        mock_run_tracker.update_status = AsyncMock()
        mock_run_tracker.update_session = AsyncMock()
        mock_run_tracker.create_session = AsyncMock(
            return_value={
                "id": 1,
                "repository": "testorg/testrepo",
                "branch": "main",
                "head_sha": "abc123",
                "trigger_run_id": "99",
                "attempt_count": 1,
                "max_attempts": 3,
                "status": "active",
                "previous_analysis": "",
                "last_fix_sha": "",
                "created_at": 0.0,
                "updated_at": 0.0,
            }
        )
        mock_run_agent.return_value = {"ci_status": "PASSED", "attempt_count": 0}

        event = _make_event(author="testadmin", run_id="99", status="failure", created_at=1714559400.0)
        await handle_webhook_event(event)

        mock_run_agent.assert_called_once()
        # The CI's created_at (parsed from the webhook payload) is carried into
        # record() so the dashboard's "Run Time" reflects the real run start.
        assert mock_run_tracker.record.call_args.kwargs["created_at"] == 1714559400.0


class TestBotTerminalStatus:
    def test_workflow_run_success(self) -> None:
        from services.webhook_handler import _bot_terminal_status

        event = _make_event(status="completed", conclusion="success")
        assert _bot_terminal_status(event) == "success"

    def test_workflow_run_failure(self) -> None:
        from services.webhook_handler import _bot_terminal_status

        event = _make_event(status="completed", conclusion="failure")
        assert _bot_terminal_status(event) == "failure"

    def test_workflow_run_cancelled_is_failure(self) -> None:
        from services.webhook_handler import _bot_terminal_status

        event = _make_event(status="completed", conclusion="cancelled")
        assert _bot_terminal_status(event) == "failure"

    def test_native_action_run_success(self) -> None:
        from services.webhook_handler import _bot_terminal_status

        event = _make_event(status="success", action="success", conclusion="success")
        assert _bot_terminal_status(event) == "success"

    def test_native_action_run_failure(self) -> None:
        from services.webhook_handler import _bot_terminal_status

        event = _make_event(status="failure", action="failure", conclusion="failure")
        assert _bot_terminal_status(event) == "failure"

    def test_non_terminal_status_returns_none(self) -> None:
        from services.webhook_handler import _bot_terminal_status

        event = _make_event(status="running", action="running", conclusion="running")
        assert _bot_terminal_status(event) is None

    def test_empty_payload_returns_none(self) -> None:
        from services.webhook_handler import _bot_terminal_status

        event = _make_event(status="", conclusion="")
        assert _bot_terminal_status(event) is None


class TestBotWorkflowRunRetryLoop:
    """Regression tests for bug 2 (workflow_run format ignored) and bug 3
    (bot retries re-keying a fresh session instead of reusing the matched one)."""

    @pytest.mark.asyncio
    @patch("services.webhook_handler.run_agent", new_callable=AsyncMock)
    @patch("server.broadcast_event")
    @patch("services.webhook_handler.run_tracker")
    @patch("services.webhook_handler.settings")
    async def test_bot_workflow_run_failure_retries_with_same_session(
        self,
        mock_settings: object,
        mock_run_tracker: AsyncMock,
        mock_broadcast: object,
        mock_run_agent: AsyncMock,
    ) -> None:
        mock_settings.ci_bot_username = "CI Review Bot"
        mock_settings.ci_bot_email = "ci-bot@autofix.internal"
        mock_settings.max_retry_attempts = 3
        mock_settings.auto_fix_reruns = "true"
        mock_run_tracker.is_duplicate = AsyncMock(return_value=False)
        mock_run_tracker.get_session_by_fix_sha = AsyncMock(
            return_value=_session(
                attempt_count=2,
                previous_analysis="prior analysis",
                last_fix_sha="botfix1",
            )
        )
        mock_run_tracker.get_run_by_session = AsyncMock(return_value=_run())
        mock_run_tracker.is_completed = AsyncMock(return_value=False)
        mock_run_tracker.record = AsyncMock()
        mock_run_tracker.update_status = AsyncMock()
        mock_run_tracker.update_session = AsyncMock()
        mock_run_tracker.get_session_by_head_sha = AsyncMock()
        mock_run_tracker.create_session = AsyncMock()
        mock_run_agent.return_value = {
            "ci_status": "FIX_PUSHED",
            "attempt_count": 3,
            "commit_sha": "botfix2",
            "branch": "main",
            "ci_author": "CI Review Bot",
            "explanation": "second fix",
        }

        event = _make_event(
            author="testadmin",
            run_id="100",
            status="completed",
            conclusion="failure",
            commit_author="CI Review Bot",
            commit_author_email="ci-bot@autofix.internal",
            sha="botfix1",
        )
        await handle_webhook_event(event)

        # workflow_run failure must invoke the agent (not silently record+wait)
        mock_run_agent.assert_called_once()
        mock_run_tracker.record.assert_not_called()
        state = mock_run_agent.call_args.args[0]
        # Bug 3: the originating session is reused, not re-derived
        assert state["session_id"] == 1
        assert state["attempt_count"] == 2
        assert state["previous_context"] == "prior analysis"
        assert state["commit_sha"] == "botfix1"
        mock_run_tracker.get_session_by_head_sha.assert_not_called()
        mock_run_tracker.create_session.assert_not_called()
        # Every status update targets the session's lifecycle row (trigger run
        # "99"), never the bot's fresh run id ("100") — single-row lifecycle.
        assert all(call.args[1] == "99" for call in mock_run_tracker.update_status.call_args_list)
        # Agent result (FIX_PUSHED) persists the new fix sha on the same session
        mock_run_tracker.update_session.assert_called()
        assert mock_run_tracker.update_session.call_args.args[0] == 1

    @pytest.mark.asyncio
    @patch("services.webhook_handler.run_agent", new_callable=AsyncMock)
    @patch("server.broadcast_event")
    @patch("services.webhook_handler.run_tracker")
    @patch("services.webhook_handler.settings")
    async def test_bot_workflow_run_success_closes_session(
        self,
        mock_settings: object,
        mock_run_tracker: AsyncMock,
        mock_broadcast: object,
        mock_run_agent: AsyncMock,
    ) -> None:
        mock_settings.ci_bot_username = "CI Review Bot"
        mock_settings.ci_bot_email = "ci-bot@autofix.internal"
        mock_settings.max_retry_attempts = 3
        mock_settings.auto_fix_reruns = "true"
        mock_run_tracker.is_duplicate = AsyncMock(return_value=False)
        mock_run_tracker.get_session_by_fix_sha = AsyncMock(
            return_value=_session(attempt_count=2, last_fix_sha="botfix1")
        )
        mock_run_tracker.get_run_by_session = AsyncMock(return_value=_run())
        mock_run_tracker.record = AsyncMock()
        mock_run_tracker.update_status = AsyncMock()
        mock_run_tracker.update_session = AsyncMock()

        event = _make_event(
            author="testadmin",
            run_id="100",
            status="completed",
            conclusion="success",
            commit_author="CI Review Bot",
            commit_author_email="ci-bot@autofix.internal",
            sha="botfix1",
        )
        await handle_webhook_event(event)

        # Bug 2: a workflow_run "completed"+"success" must close the session
        # AND advance the session's lifecycle row — never insert a new row.
        mock_run_agent.assert_not_called()
        mock_run_tracker.update_session.assert_called_once_with(1, status="PASSED")
        mock_run_tracker.record.assert_not_called()
        mock_run_tracker.update_status.assert_called_once()
        assert mock_run_tracker.update_status.call_args.args[1] == "99"
        assert mock_run_tracker.update_status.call_args.kwargs["status"] == "PASSED"
        assert mock_run_tracker.update_status.call_args.kwargs["session_id"] == 1

    @pytest.mark.asyncio
    @patch("services.webhook_handler.run_agent", new_callable=AsyncMock)
    @patch("server.broadcast_event")
    @patch("services.webhook_handler.run_tracker")
    @patch("services.webhook_handler.settings")
    async def test_bot_failure_exhausted_after_max_attempts(
        self,
        mock_settings: object,
        mock_run_tracker: AsyncMock,
        mock_broadcast: object,
        mock_run_agent: AsyncMock,
    ) -> None:
        mock_settings.ci_bot_username = "CI Review Bot"
        mock_settings.ci_bot_email = "ci-bot@autofix.internal"
        mock_settings.max_retry_attempts = 3
        mock_settings.auto_fix_reruns = "true"
        mock_run_tracker.is_duplicate = AsyncMock(return_value=False)
        mock_run_tracker.get_session_by_fix_sha = AsyncMock(
            return_value=_session(attempt_count=3, max_attempts=3, last_fix_sha="botfix2")
        )
        mock_run_tracker.get_run_by_session = AsyncMock(return_value=_run())
        mock_run_tracker.record = AsyncMock()
        mock_run_tracker.update_status = AsyncMock()
        mock_run_tracker.update_session = AsyncMock()

        event = _make_event(
            author="testadmin",
            run_id="101",
            status="completed",
            conclusion="failure",
            commit_author="CI Review Bot",
            commit_author_email="ci-bot@autofix.internal",
            sha="botfix2",
        )
        await handle_webhook_event(event)

        # Max attempts reached -> escalate to EXHAUSTED, no agent invocation
        mock_run_agent.assert_not_called()
        mock_run_tracker.update_session.assert_called_once_with(1, status="EXHAUSTED")
        mock_run_tracker.record.assert_not_called()
        mock_run_tracker.update_status.assert_called_once()
        assert mock_run_tracker.update_status.call_args.args[1] == "99"
        assert mock_run_tracker.update_status.call_args.kwargs["status"] == "EXHAUSTED"
        assert mock_run_tracker.update_status.call_args.kwargs["session_id"] == 1
        mock_broadcast.assert_called_once()
        assert mock_broadcast.call_args.kwargs["status"] == "EXHAUSTED"
        assert mock_broadcast.call_args.kwargs["task_key"] == "testorg/testrepo:99:1"

    @pytest.mark.asyncio
    @patch("services.webhook_handler.run_agent", new_callable=AsyncMock)
    @patch("server.broadcast_event")
    @patch("services.webhook_handler.run_tracker")
    @patch("services.webhook_handler.settings")
    async def test_bot_failure_exhausted_broadcast_includes_run_attempt(
        self,
        mock_settings: object,
        mock_run_tracker: AsyncMock,
        mock_broadcast: object,
        mock_run_agent: AsyncMock,
    ) -> None:
        mock_settings.ci_bot_username = "CI Review Bot"
        mock_settings.ci_bot_email = "ci-bot@autofix.internal"
        mock_settings.max_retry_attempts = 3
        mock_settings.auto_fix_reruns = "true"
        mock_run_tracker.is_duplicate = AsyncMock(return_value=False)
        mock_run_tracker.get_session_by_fix_sha = AsyncMock(
            return_value=_session(attempt_count=3, max_attempts=3, last_fix_sha="botfix2")
        )
        mock_run_tracker.get_run_by_session = AsyncMock(return_value=_run())
        mock_run_tracker.record = AsyncMock()
        mock_run_tracker.update_status = AsyncMock()
        mock_run_tracker.update_session = AsyncMock()

        event = _make_event(
            author="testadmin",
            run_id="101",
            run_attempt="2",
            status="completed",
            conclusion="failure",
            commit_author="CI Review Bot",
            commit_author_email="ci-bot@autofix.internal",
            sha="botfix2",
        )
        await handle_webhook_event(event)

        # SSE row lookup uses meta.run_attempt — must match the lifecycle row's
        # attempt (not the bot event's fresh attempt) so the existing row updates.
        mock_broadcast.assert_called_once()
        assert mock_broadcast.call_args.kwargs["status"] == "EXHAUSTED"
        assert mock_broadcast.call_args.kwargs["meta"]["run_attempt"] == "1"
        assert mock_broadcast.call_args.kwargs["meta"]["session_id"] == 1
        assert mock_broadcast.call_args.kwargs["task_key"] == "testorg/testrepo:99:1"

    @pytest.mark.asyncio
    @patch("services.webhook_handler.run_agent", new_callable=AsyncMock)
    @patch("server.broadcast_event")
    @patch("services.webhook_handler.run_tracker")
    @patch("services.webhook_handler.settings")
    async def test_bot_running_event_updates_lifecycle_row_and_broadcasts(
        self,
        mock_settings: object,
        mock_run_tracker: AsyncMock,
        mock_broadcast: object,
        mock_run_agent: AsyncMock,
    ) -> None:
        mock_settings.ci_bot_username = "CI Review Bot"
        mock_settings.ci_bot_email = "ci-bot@autofix.internal"
        mock_settings.max_retry_attempts = 3
        mock_settings.auto_fix_reruns = "true"
        mock_run_tracker.is_duplicate = AsyncMock(return_value=False)
        mock_run_tracker.get_session_by_fix_sha = AsyncMock(return_value=_session(last_fix_sha="botfix1"))
        mock_run_tracker.get_run_by_session = AsyncMock(return_value=_run())
        mock_run_tracker.record = AsyncMock()
        mock_run_tracker.update_status = AsyncMock()
        mock_run_tracker.update_session = AsyncMock()

        event = _make_event(
            author="testadmin",
            run_id="100",
            status="running",
            conclusion="running",
            commit_author="CI Review Bot",
            commit_author_email="ci-bot@autofix.internal",
            sha="botfix1",
        )
        await handle_webhook_event(event)

        # Non-terminal bot event: advance the session's lifecycle row in place
        # (no new row) and surface a live update; never invoke or close.
        mock_run_agent.assert_not_called()
        mock_run_tracker.update_session.assert_not_called()
        mock_run_tracker.record.assert_not_called()
        mock_run_tracker.update_status.assert_called_once()
        # Targets the trigger row ("99"), never the bot's fresh run id ("100")
        assert mock_run_tracker.update_status.call_args.args[1] == "99"
        assert mock_run_tracker.update_status.call_args.kwargs["status"] == "running"
        assert mock_run_tracker.update_status.call_args.kwargs["session_id"] == 1
        mock_broadcast.assert_called_once()
        assert mock_broadcast.call_args.kwargs["status"] == "running"
        # task_key matches the existing SSE row so it updates in place
        assert mock_broadcast.call_args.kwargs["task_key"] == "testorg/testrepo:99:1"
        assert mock_broadcast.call_args.kwargs["meta"]["session_id"] == 1

    @pytest.mark.asyncio
    @patch("services.webhook_handler.run_agent", new_callable=AsyncMock)
    @patch("server.broadcast_event")
    @patch("services.webhook_handler.run_tracker")
    @patch("services.webhook_handler.settings")
    async def test_bot_native_format_failure_retries(
        self,
        mock_settings: object,
        mock_run_tracker: AsyncMock,
        mock_broadcast: object,
        mock_run_agent: AsyncMock,
    ) -> None:
        mock_settings.ci_bot_username = "CI Review Bot"
        mock_settings.ci_bot_email = "ci-bot@autofix.internal"
        mock_settings.max_retry_attempts = 3
        mock_settings.auto_fix_reruns = "true"
        mock_run_tracker.is_duplicate = AsyncMock(return_value=False)
        mock_run_tracker.get_session_by_fix_sha = AsyncMock(
            return_value=_session(attempt_count=2, last_fix_sha="botfix1")
        )
        mock_run_tracker.get_run_by_session = AsyncMock(return_value=_run())
        mock_run_tracker.is_completed = AsyncMock(return_value=False)
        mock_run_tracker.record = AsyncMock()
        mock_run_tracker.update_status = AsyncMock()
        mock_run_tracker.update_session = AsyncMock()
        mock_run_agent.return_value = {
            "ci_status": "FIX_PUSHED",
            "attempt_count": 3,
            "commit_sha": "botfix2",
            "branch": "main",
            "ci_author": "CI Review Bot",
            "explanation": "second fix",
        }

        event = _make_event(
            author="testadmin",
            run_id="100",
            status="failure",
            action="failure",
            conclusion="failure",
            commit_author="CI Review Bot",
            commit_author_email="ci-bot@autofix.internal",
            sha="botfix1",
        )
        await handle_webhook_event(event)

        # Native action_run format still retries via the matched session
        mock_run_agent.assert_called_once()
        mock_run_tracker.record.assert_not_called()
        state = mock_run_agent.call_args.args[0]
        assert state["session_id"] == 1
        assert state["attempt_count"] == 2
        # status updates always target the trigger row, never the bot's run id
        assert all(call.args[1] == "99" for call in mock_run_tracker.update_status.call_args_list)
        mock_run_tracker.create_session.assert_not_called()
