from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # GitHub / Forgejo
    github_token: str = ""
    forgejo_token: str = ""
    forgejo_base_url: str = "https://forgejo.example.com"

    # Messaging platform (mattermost | slack | discord)
    messaging_platform: str = "mattermost"

    # MCP Server (Go binary)
    mcp_server_command: str = "./universal-messaging-mcp"
    mcp_server_args: list[str] = ["-transport", "stdio"]
    mcp_server_env: dict[str, str] | None = None

    # Webhook URLs (passed to MCP server subprocess)
    mattermost_webhook_url: str = ""
    slack_webhook_url: str = ""
    discord_webhook_url: str = ""

    # LLM
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-7-sonnet-latest"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.3"
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = ""
    llm_provider: str = "openai"  # "openai", "anthropic", "ollama", "azure_openai"

    # Agent Engine Options
    max_retry_attempts: int = 3
    poll_interval_seconds: int = 30
    poll_max_wait_seconds: int = 600
    log_max_tokens: int = 4000
    checkpointer_type: str = "sqlite"  # "sqlite" or "memory"
    notification_trigger_level: str = "failures_only"  # "always", "failures_only", "success_only", "never"
    auto_create_pull_request: str = "true"  # "true" or "false"

    # Webhook secrets (for signature verification)
    forgejo_webhook_secret: str = ""
    github_webhook_secret: str = ""

    # Server
    server_host: str = "127.0.0.1"
    server_port: int = 8000

    # Git
    git_repo_path: str = "."
    git_default_branch: str = "main"

    @property
    def mcp_server_env_with_webhooks(self) -> dict[str, str]:
        env: dict[str, str] = {}
        if self.mattermost_webhook_url:
            env["MATTERMOST_WEBHOOK_URL"] = self.mattermost_webhook_url
        if self.slack_webhook_url:
            env["SLACK_WEBHOOK_URL"] = self.slack_webhook_url
        if self.discord_webhook_url:
            env["DISCORD_WEBHOOK_URL"] = self.discord_webhook_url
        if self.mcp_server_env:
            env.update(self.mcp_server_env)
        return env


settings = Settings()
