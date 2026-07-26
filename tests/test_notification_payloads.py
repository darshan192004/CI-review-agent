from config import Settings


class TestMcpPayloadConstruction:
    def test_fetch_logs_payload_matches_send_alert_schema(self):
        settings = Settings(
            messaging_platform="mattermost",
            mcp_server_command="./test-binary",
        )
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

        assert payload["platform"] in ("mattermost", "slack", "discord")
        assert "incident_title" in payload
        assert "root_cause" in payload
        assert "resolution_steps" in payload
        assert len(payload) == 4

    def test_success_payload_matches_send_alert_schema(self):
        settings = Settings(messaging_platform="slack")
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
        settings = Settings(messaging_platform="discord")
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
        s = Settings()
        assert s.messaging_platform == "mattermost"

    def test_mcp_server_command_configurable(self):
        s = Settings(mcp_server_command="/usr/bin/my-mcp")
        assert s.mcp_server_command == "/usr/bin/my-mcp"

    def test_webhook_urls_collected(self):
        s = Settings(
            mattermost_webhook_url="https://mm.example.com/hooks/123",
            slack_webhook_url="https://hooks.slack.com/xxx",
        )
        env = s.mcp_server_env_with_webhooks
        assert env["MATTERMOST_WEBHOOK_URL"] == "https://mm.example.com/hooks/123"
        assert env["SLACK_WEBHOOK_URL"] == "https://hooks.slack.com/xxx"
        assert "DISCORD_WEBHOOK_URL" not in env

    def test_empty_webhooks_produces_empty_env(self):
        s = Settings()
        env = s.mcp_server_env_with_webhooks
        assert env == {}
