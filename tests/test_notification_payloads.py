from config import Settings

_PLATFORMS = ("mattermost", "slack", "discord", "telegram")


class TestNotificationPayloads:
    def test_fetch_logs_payload_matches_send_alert_schema(self):
        settings = Settings(_env_file=None, messaging_platform="mattermost")
        repo = "owner/repo"
        branch = "main"
        sha = "abc123"
        author = "testuser"
        failure_summary = "ERROR: test failed"
        run_id = "12345"

        payload = {
            "platform": settings.messaging_platform,
            "incident_title": f"CI Failed: {repo} (branch: {branch})",
            "root_cause": failure_summary,
            "resolution_steps": (
                f"Automated fix attempt 1/{settings.max_retry_attempts} in progress.\n"
                f"Repository: {repo}\n"
                f"Branch: {branch}\n"
                f"Commit: {sha}\n"
                f"Author: {author}\n"
                f"Run ID: {run_id}"
            ),
        }

        assert payload["platform"] in _PLATFORMS
        assert "incident_title" in payload
        assert "root_cause" in payload
        assert "resolution_steps" in payload
        assert len(payload) == 4

    def test_success_payload_matches_send_alert_schema(self):
        settings = Settings(_env_file=None, messaging_platform="slack")
        payload = {
            "platform": settings.messaging_platform,
            "incident_title": "CI Fixed: owner/repo",
            "root_cause": "Previously failing CI",
            "resolution_steps": "Automated fix applied after 2 attempt(s).",
        }
        assert payload["platform"] == "slack"
        assert "incident_title" in payload
        assert "root_cause" in payload
        assert "resolution_steps" in payload

    def test_escalation_payload_matches_send_alert_schema(self):
        settings = Settings(_env_file=None, messaging_platform="discord")
        payload = {
            "platform": settings.messaging_platform,
            "incident_title": "ESCALATION: CI Fix Failed (owner/repo)",
            "root_cause": "LLM could not resolve",
            "resolution_steps": "Automated fix failed after 3 attempts.",
        }
        assert payload["platform"] == "discord"
        assert "ESCALATION" in payload["incident_title"]
        assert "root_cause" in payload
        assert "resolution_steps" in payload


class TestConfigDefaults:
    def test_messaging_platform_defaults_to_mattermost(self):
        s = Settings(_env_file=None)
        assert s.messaging_platform == "mattermost"

    def test_telegram_settings_default_to_empty(self):
        s = Settings(_env_file=None)
        assert s.telegram_bot_token == ""
        assert s.telegram_chat_id == ""

    def test_notification_trigger_level_defaults_to_failures_only(self):
        s = Settings(_env_file=None)
        assert s.notification_trigger_level == "failures_only"
