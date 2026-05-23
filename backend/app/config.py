from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central configuration for the AisoFlow backend.

    Values are loaded from environment variables and, during local
    development, from a .env file placed inside the backend directory.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = Field(default="AisoFlow", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    app_debug: bool = Field(default=True, alias="APP_DEBUG")

    # API
    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")

    # Local storage
    workspaces_dir: Path = Field(default=Path("./workspaces"), alias="WORKSPACES_DIR")
    logs_dir: Path = Field(default=Path("./logs"), alias="LOGS_DIR")
    agentdock_db_path: Path = Field(
        default=Path("./data/agentdock.sqlite3"),
        alias="AGENTDOCK_DB_PATH",
    )

    # GitHub integration
    github_token: str | None = Field(default=None, alias="GITHUB_TOKEN")
    github_owner: str | None = Field(default=None, alias="GITHUB_OWNER")
    github_default_branch: str = Field(default="main", alias="GITHUB_DEFAULT_BRANCH")
    github_app_id: str | None = Field(default=None, alias="GITHUB_APP_ID")
    github_app_slug: str | None = Field(default=None, alias="GITHUB_APP_SLUG")
    github_app_private_key_path: Path | None = Field(
        default=None,
        alias="GITHUB_APP_PRIVATE_KEY_PATH",
    )
    github_app_client_id: str | None = Field(
        default=None,
        alias="GITHUB_APP_CLIENT_ID",
    )
    github_app_client_secret: str | None = Field(
        default=None,
        alias="GITHUB_APP_CLIENT_SECRET",
    )
    github_app_install_url: str | None = Field(
        default=None,
        alias="GITHUB_APP_INSTALL_URL",
    )
    github_app_callback_url: str = Field(
        default="http://127.0.0.1:8000/auth/github-app/callback",
        alias="GITHUB_APP_CALLBACK_URL",
    )
    github_pr_merge_method: str = Field(default="squash", alias="GITHUB_PR_MERGE_METHOD")
    delete_branch_after_merge: bool = Field(
        default=False,
        alias="DELETE_BRANCH_AFTER_MERGE",
    )
    github_oauth_client_id: str | None = Field(
        default=None,
        alias="GITHUB_OAUTH_CLIENT_ID",
    )
    github_oauth_client_secret: str | None = Field(
        default=None,
        alias="GITHUB_OAUTH_CLIENT_SECRET",
    )
    github_oauth_redirect_uri: str = Field(
        default="http://127.0.0.1:8000/auth/github/callback",
        alias="GITHUB_OAUTH_REDIRECT_URI",
    )

    # AI provider
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    prompt_assistant_openai_model: str = Field(
        default="gpt-5.5",
        alias="PROMPT_ASSISTANT_OPENAI_MODEL",
    )
    prompt_assistant_anthropic_model: str = Field(
        default="claude-sonnet-4-5",
        alias="PROMPT_ASSISTANT_ANTHROPIC_MODEL",
    )
    ollama_base_url: str | None = Field(default=None, alias="OLLAMA_BASE_URL")
    ai_provider: str = Field(default="mock", alias="AI_PROVIDER")
    agent_runner: str = Field(default="mock", alias="AGENT_RUNNER")
    local_runner_command: str | None = Field(
        default=None,
        alias="LOCAL_RUNNER_COMMAND",
    )
    local_runner_timeout_seconds: int | None = Field(
        default=None,
        alias="LOCAL_RUNNER_TIMEOUT_SECONDS",
    )
    codex_cli_command: str = Field(default="codex", alias="CODEX_CLI_COMMAND")
    codex_enabled: bool = Field(default=False, alias="CODEX_ENABLED")
    codex_timeout_seconds: int = Field(default=900, alias="CODEX_TIMEOUT_SECONDS")
    # Safety
    allow_push: bool = Field(default=False, alias="ALLOW_PUSH")
    allow_pr_creation: bool = Field(default=False, alias="ALLOW_PR_CREATION")
    max_task_runtime_seconds: int = Field(
        default=900,
        alias="MAX_TASK_RUNTIME_SECONDS",
    )

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() == "development"

    @property
    def workspace_root(self) -> Path:
        return self.workspaces_dir.resolve()

    @property
    def logs_root(self) -> Path:
        return self.logs_dir.resolve()

    @property
    def db_path(self) -> Path:
        return self.agentdock_db_path.resolve()

    def ensure_local_directories(self) -> None:
        """
        Ensure local runtime directories exist.

        These folders are ignored by git and used only for local task runs.
        """
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.logs_root.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """
    Return cached application settings.

    FastAPI dependencies can use this so the settings object is only created once.
    """
    settings = Settings()
    settings.ensure_local_directories()
    return settings
