from __future__ import annotations

import logging
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


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

    # LLM — Tier 1 (fully tested)
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-7-sonnet-latest"
    bedrock_aws_access_key_id: str = ""
    bedrock_aws_secret_access_key: str = ""
    bedrock_region: str = "us-east-1"
    bedrock_model: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2024-08-01-preview"
    azure_openai_deployment: str = ""

    # LLM — Tier 2 (best-effort)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    mistral_api_key: str = ""
    mistral_model: str = "mistral-large-latest"
    cohere_api_key: str = ""
    cohere_model: str = "command-r-plus"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    together_api_key: str = ""
    together_model: str = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com"
    xai_api_key: str = ""
    xai_model: str = "grok-2"
    xai_base_url: str = "https://api.x.ai/v1"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.3"

    llm_provider: str = "openai"  # openai | anthropic | bedrock | azure_openai | gemini | mistral | cohere | groq | together | deepseek | xai | ollama  # noqa: E501

    # Agent Engine Options
    max_retry_attempts: int = 3
    poll_interval_seconds: int = 30
    poll_max_wait_seconds: int = 600
    log_max_tokens: int = 4000
    checkpointer_type: str = "sqlite"  # "sqlite" or "memory"
    notification_trigger_level: str = "failures_only"  # "always", "failures_only", "success_only", "never"
    auto_create_pull_request: str = "true"  # "true" or "false"
    auto_fix_reruns: str = "true"  # "true" or "false" — trigger agent auto-fix on rerun webhooks

    # Webhook secrets (for signature verification)
    forgejo_webhook_secret: str = ""
    github_webhook_secret: str = ""

    # Server
    server_host: str = "127.0.0.1"
    server_port: int = 8000

    # Git
    git_repo_path: str = "."
    git_default_branch: str = "main"
    git_clone_url: str = ""
    git_pat_token: str = ""
    git_clone_depth: int = 1

    # Authentication
    secret_key: str = ""
    admin_username: str = ""
    admin_password: str = ""
    viewer_username: str = ""
    viewer_password: str = ""

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

    def model_post_init(self, _context: object) -> None:
        if self.mcp_server_command:
            cmd_path = Path(self.mcp_server_command)
            if not cmd_path.is_file():
                logger.warning(
                    "MCP server binary not found at '%s'. "
                    "Ping MCP and Send Test Alert will fail. "
                    "Set MCP_SERVER_COMMAND in .env to the correct absolute path.",
                    self.mcp_server_command,
                )


settings = Settings()
