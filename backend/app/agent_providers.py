from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath
import shlex
import shutil
import sqlite3
import subprocess

from app.config import Settings
from app.models import (
    AgentProviderConnectionStatus,
    AgentProviderMetadataResponse,
    AgentProviderReadiness,
    AgentProviderStatus,
    AgentProviderStatusResponse,
)


class AgentProviderError(Exception):
    """
    Raised when an agent provider cannot complete a task.
    """


class AgentRunResult:
    """
    Result returned after an agent provider has attempted to apply a task.
    """

    def __init__(
        self,
        success: bool,
        message: str,
        modified_files: list[str] | None = None,
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        self.success = success
        self.message = message
        self.modified_files = modified_files or []
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.duration_seconds = duration_seconds

    def log_messages(self) -> list[tuple[str, str]]:
        """
        Return concise task log entries for provider diagnostics.
        """

        messages: list[tuple[str, str]] = []

        if self.duration_seconds is not None:
            messages.append(("info", f"Runner duration: {self.duration_seconds:.2f}s."))

        if self.exit_code is not None:
            level = "info" if self.exit_code == 0 else "error"
            messages.append((level, f"Runner exit code: {self.exit_code}."))

        if self.stdout.strip():
            messages.append(("info", f"Runner stdout:\n{self.stdout.strip()}"))

        if self.stderr.strip():
            level = "info" if self.success else "error"
            messages.append((level, f"Runner stderr:\n{self.stderr.strip()}"))

        return messages


class AgentProviderBase:
    """
    Base interface for coding agent providers.
    """

    def run(
        self,
        repo_path: Path,
        instruction: str,
        target_file: str | None = None,
        change_scope: str = "only_selected_file",
    ) -> AgentRunResult:
        """
        Run the agent provider against a repository path.
        """

        raise NotImplementedError


class MockProvider(AgentProviderBase):
    """
    Deterministic development provider used until real integrations are wired in.
    """

    TASK_NOTES_FILE = "AGENTDOCK_TASK.md"

    def run(
        self,
        repo_path: Path,
        instruction: str,
        target_file: str | None = None,
        change_scope: str = "only_selected_file",
    ) -> AgentRunResult:
        if not repo_path.exists() or not repo_path.is_dir():
            raise AgentProviderError(f"Repository path does not exist: {repo_path}")

        if target_file:
            return self._append_target_file_note(
                repo_path=repo_path,
                target_file=target_file,
                instruction=instruction,
                change_scope=change_scope,
            )

        note_path = repo_path / self.TASK_NOTES_FILE
        timestamp = datetime.now(timezone.utc).isoformat()

        content = self._build_task_note(
            instruction=instruction,
            timestamp=timestamp,
        )

        try:
            note_path.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise AgentProviderError(
                f"Failed to write development agent output: {note_path}"
            ) from exc

        return AgentRunResult(
            success=True,
            message="Agent completed successfully.",
            modified_files=[self.TASK_NOTES_FILE],
        )

    def _append_target_file_note(
        self,
        repo_path: Path,
        target_file: str,
        instruction: str,
        change_scope: str,
    ) -> AgentRunResult:
        target_path = self._target_path(repo_path, target_file)
        marker = self._marker_for_file(
            target_path=target_path,
            instruction=instruction,
            change_scope=change_scope,
        )

        try:
            existing = target_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise AgentProviderError(
                f"Development agent can only modify UTF-8 text files: {target_file}"
            ) from exc
        except OSError as exc:
            raise AgentProviderError(
                f"Failed to read selected target file: {target_file}"
            ) from exc

        separator = "" if existing.endswith("\n") else "\n"

        try:
            target_path.write_text(
                f"{existing}{separator}{marker}",
                encoding="utf-8",
            )
        except OSError as exc:
            raise AgentProviderError(
                f"Failed to append development note to selected target file: {target_file}"
            ) from exc

        return AgentRunResult(
            success=True,
            message="Agent completed successfully.",
            modified_files=[target_file],
        )

    def _target_path(self, repo_path: Path, target_file: str) -> Path:
        if "\\" in target_file:
            raise AgentProviderError("Invalid target file path.")

        posix_path = PurePosixPath(target_file)

        if posix_path.is_absolute() or ".." in posix_path.parts:
            raise AgentProviderError("Invalid target file path.")

        target_path = (repo_path / Path(*posix_path.parts)).resolve()

        try:
            target_path.relative_to(repo_path.resolve())
        except ValueError as exc:
            raise AgentProviderError("Target file is outside the repository.") from exc

        if not target_path.is_file():
            raise AgentProviderError(f"Selected target file does not exist: {target_file}")

        return target_path

    def _marker_for_file(
        self,
        target_path: Path,
        instruction: str,
        change_scope: str,
    ) -> str:
        safe_instruction = " ".join(instruction.strip().split())
        if len(safe_instruction) > 100:
            safe_instruction = f"{safe_instruction[:97]}..."

        extension = target_path.suffix.lower()
        note = (
            "AgentDock development note: target-aware execution touched this "
            f"file for review. Scope={change_scope}. Prompt={safe_instruction}"
        )

        if extension == ".py":
            return f"# TODO(AgentDock): {note}\n"

        if extension in {".ts", ".tsx", ".js", ".jsx"}:
            return f"// TODO(AgentDock): {note}\n"

        if extension == ".css":
            return f"/* TODO(AgentDock): {note} */\n"

        if extension == ".md":
            return f"\n> TODO(AgentDock): {note}\n"

        return f"\n{note}\n"

    def _build_task_note(
        self,
        instruction: str,
        timestamp: str,
    ) -> str:
        return (
            "# AgentDock Task Note\n\n"
            "This file was generated by the AgentDock development provider.\n\n"
            "## Instruction\n\n"
            f"{instruction.strip()}\n\n"
            "## Status\n\n"
            "- Provider: development\n"
            "- Result: completed\n"
            f"- Timestamp: {timestamp}\n\n"
            "## Next Step\n\n"
            "Review the generated diff before approving any commit.\n"
        )


class AgentProviderConnectionStore:
    """
    SQLite-backed local connection state for provider UX scaffolding.
    """

    def __init__(self, settings: Settings) -> None:
        self.db_path = settings.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self, provider_id: str, auth_mode: str) -> None:
        with self._connect() as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO agent_provider_connections (
                        provider_id,
                        auth_mode,
                        connected_at
                    )
                    VALUES (?, ?, ?)
                    ON CONFLICT(provider_id) DO UPDATE SET
                        auth_mode = excluded.auth_mode,
                        connected_at = excluded.connected_at
                    """,
                    (provider_id, auth_mode, datetime.now(timezone.utc).isoformat()),
                )

    def disconnect(self, provider_id: str) -> None:
        with self._connect() as connection:
            with connection:
                connection.execute(
                    "DELETE FROM agent_provider_connections WHERE provider_id = ?",
                    (provider_id,),
                )

    def connected_auth_modes(self) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT provider_id, auth_mode FROM agent_provider_connections"
            ).fetchall()

        return {str(row["provider_id"]): str(row["auth_mode"]) for row in rows}

    def _initialize(self) -> None:
        with self._connect() as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_provider_connections (
                        provider_id TEXT PRIMARY KEY,
                        auth_mode TEXT NOT NULL,
                        connected_at TEXT NOT NULL
                    )
                    """
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection


def provider_metadata(settings: Settings) -> list[AgentProviderMetadataResponse]:
    """
    Return user-facing provider metadata and simulated connection state.
    """

    connected_auth_modes = AgentProviderConnectionStore(settings).connected_auth_modes()
    codex_ready = _codex_cli_ready(settings)
    codex_cli_available = _codex_cli_available(settings)
    codex_readiness = (
        AgentProviderReadiness.ready
        if codex_ready
        else AgentProviderReadiness.not_configured
    )
    claude_readiness = (
        AgentProviderReadiness.unavailable
        if settings.anthropic_api_key
        else AgentProviderReadiness.not_configured
    )
    ollama_readiness = (
        AgentProviderReadiness.ready
        if settings.ollama_base_url
        else AgentProviderReadiness.not_configured
    )
    local_model_readiness = (
        AgentProviderReadiness.ready
        if settings.local_runner_command
        else AgentProviderReadiness.not_configured
    )

    providers = [
        _metadata(
            provider_id="codex",
            label="OpenAI Codex",
            auth_mode=connected_auth_modes.get("codex", "codex_cli"),
            connection=(
                AgentProviderConnectionStatus.connected
                if "codex" in connected_auth_modes
                else AgentProviderConnectionStatus.disconnected
            ),
            readiness=codex_readiness,
            description=(
                "Use local Codex CLI with your ChatGPT/OpenAI login."
            ),
            supports_chatgpt_login=False,
            supports_api_key=False,
        ),
        _metadata(
            provider_id="claude",
            label="Claude",
            auth_mode=connected_auth_modes.get("claude", "claude_login_or_api_key"),
            connection=(
                AgentProviderConnectionStatus.connected
                if "claude" in connected_auth_modes
                else AgentProviderConnectionStatus.disconnected
            ),
            readiness=claude_readiness,
            description="Claude provider integration placeholder.",
            supports_chatgpt_login=False,
            supports_api_key=True,
        ),
        _metadata(
            provider_id="ollama",
            label="Ollama",
            auth_mode=connected_auth_modes.get("ollama", "local_url"),
            connection=(
                AgentProviderConnectionStatus.connected
                if "ollama" in connected_auth_modes
                else AgentProviderConnectionStatus.disconnected
            ),
            readiness=ollama_readiness,
            description="Requires local Ollama server.",
            supports_chatgpt_login=False,
            supports_api_key=False,
        ),
        _metadata(
            provider_id="local_model",
            label="Local model",
            auth_mode=connected_auth_modes.get("local_model", "local_runner"),
            connection=(
                AgentProviderConnectionStatus.connected
                if "local_model" in connected_auth_modes
                else AgentProviderConnectionStatus.disconnected
            ),
            readiness=local_model_readiness,
            description="Requires local runner configuration.",
            supports_chatgpt_login=False,
            supports_api_key=False,
        ),
    ]

    return providers


def connect_provider(
    settings: Settings,
    provider_id: str,
    auth_mode: str | None = None,
) -> AgentProviderMetadataResponse:
    valid_provider_ids = {provider.id for provider in provider_metadata(settings)}

    if provider_id not in valid_provider_ids:
        raise AgentProviderError(f"Unsupported agent provider: {provider_id}")

    metadata = next(
        provider for provider in provider_metadata(settings) if provider.id == provider_id
    )

    if metadata.status == AgentProviderConnectionStatus.coming_soon:
        raise AgentProviderError(f"Agent provider is coming soon: {provider_id}")

    if metadata.readiness != AgentProviderReadiness.ready:
        raise AgentProviderError(f"Agent provider is not ready: {provider_id}")

    AgentProviderConnectionStore(settings).connect(
        provider_id,
        auth_mode or metadata.auth_mode,
    )
    return next(
        provider for provider in provider_metadata(settings) if provider.id == provider_id
    )


def disconnect_provider(
    settings: Settings,
    provider_id: str,
) -> AgentProviderMetadataResponse:
    valid_provider_ids = {provider.id for provider in provider_metadata(settings)}

    if provider_id not in valid_provider_ids:
        raise AgentProviderError(f"Unsupported agent provider: {provider_id}")

    AgentProviderConnectionStore(settings).disconnect(provider_id)
    return next(
        provider for provider in provider_metadata(settings) if provider.id == provider_id
    )


def _metadata(
    provider_id: str,
    label: str,
    auth_mode: str,
    connection: AgentProviderConnectionStatus,
    readiness: AgentProviderReadiness,
    description: str,
    supports_chatgpt_login: bool,
    supports_api_key: bool,
) -> AgentProviderMetadataResponse:
    return AgentProviderMetadataResponse(
        id=provider_id,
        label=label,
        auth_mode=auth_mode,
        status=connection,
        connection=connection,
        readiness=readiness,
        description=description,
        supports_chatgpt_login=supports_chatgpt_login,
        supports_api_key=supports_api_key,
    )


def provider_statuses(settings: Settings) -> list[AgentProviderStatusResponse]:
    """
    Return configuration status for user-facing agent provider choices.
    """

    codex_ready = _codex_cli_ready(settings)
    codex_cli_available = _codex_cli_available(settings)
    codex_status = (
        AgentProviderStatus.available
        if codex_ready
        else AgentProviderStatus.not_configured
    )
    claude_status = (
        AgentProviderStatus.available
        if settings.anthropic_api_key
        else AgentProviderStatus.not_configured
    )
    ollama_status = (
        AgentProviderStatus.available
        if settings.ollama_base_url
        else AgentProviderStatus.local_required
    )
    local_model_status = (
        AgentProviderStatus.available
        if settings.local_runner_command
        else AgentProviderStatus.local_required
    )

    providers = [
        AgentProviderStatusResponse(
            id="codex",
            label="Codex",
            status=codex_status,
            description=(
                "Runs the local Codex CLI inside the task workspace when enabled."
                if codex_ready
                else "Requires CODEX_ENABLED=true, the Codex CLI, and local Codex authentication."
            ),
            required_config=[]
            if codex_ready
            else _codex_required_config(settings, codex_cli_available),
        ),
        AgentProviderStatusResponse(
            id="claude",
            label="Claude",
            status=claude_status,
            description=(
                "Requires Anthropic credentials. Not connected to task execution yet."
            ),
            required_config=[] if settings.anthropic_api_key else ["ANTHROPIC_API_KEY"],
        ),
        AgentProviderStatusResponse(
            id="ollama",
            label="Ollama",
            status=ollama_status,
            description=(
                "Requires a reachable local Ollama service. Not connected to task execution yet."
            ),
            required_config=[] if settings.ollama_base_url else ["OLLAMA_BASE_URL"],
        ),
        AgentProviderStatusResponse(
            id="local_model",
            label="Local model",
            status=local_model_status,
            description=(
                "Requires a local command/model adapter."
            ),
            required_config=[]
            if settings.local_runner_command
            else ["LOCAL_RUNNER_COMMAND"],
        ),
    ]

    if settings.is_development:
        providers.append(
            AgentProviderStatusResponse(
                id="development",
                label="Development agent",
                status=AgentProviderStatus.available,
                description=(
                    "Tasks currently run using the local development agent."
                ),
                required_config=[],
            )
        )

    return providers


def _codex_cli_ready(settings: Settings) -> bool:
    return (
        settings.codex_enabled
        and _codex_cli_available(settings)
        and _codex_cli_authenticated(settings)
    )


def _codex_cli_available(settings: Settings) -> bool:
    try:
        command = shlex.split(settings.codex_cli_command)
    except ValueError:
        return False

    return bool(command and shutil.which(command[0]))


def _codex_cli_authenticated(settings: Settings) -> bool:
    if settings.openai_api_key:
        return True

    try:
        command = shlex.split(settings.codex_cli_command)
    except ValueError:
        return False

    if not command or not shutil.which(command[0]):
        return False

    try:
        completed = subprocess.run(
            [*command, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False

    return completed.returncode == 0


def _codex_required_config(
    settings: Settings,
    codex_cli_available: bool,
) -> list[str]:
    required: list[str] = []

    if not settings.codex_enabled:
        required.append("CODEX_ENABLED")

    if not codex_cli_available:
        required.append("CODEX_CLI_COMMAND")

    if codex_cli_available and not _codex_cli_authenticated(settings):
        required.append("CODEX_AUTH")

    return required


def _codex_readiness_description(
    settings: Settings,
    codex_cli_available: bool,
) -> str:
    missing = _codex_required_config(settings, codex_cli_available)

    if "CODEX_ENABLED" in missing:
        return "Disconnected. Set CODEX_ENABLED=true to enable the local Codex CLI."

    if "CODEX_CLI_COMMAND" in missing:
        return "Disconnected. Codex CLI is not available from CODEX_CLI_COMMAND."

    if "CODEX_AUTH" in missing:
        return "Disconnected. Authenticate the Codex CLI outside AgentDock."

    return "Disconnected. Connect this provider in AgentDock to use the Codex CLI."
