from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central configuration for the AgentDock backend.

    Values are loaded from environment variables and, during local
    development, from a .env file placed inside the backend directory.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = Field(default="AgentDock", alias="APP_NAME")
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
