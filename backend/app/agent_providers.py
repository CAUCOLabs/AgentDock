from datetime import datetime, timezone
import hashlib
import logging
import os
from pathlib import Path
from pathlib import PurePosixPath
import queue
import re
import shlex
import shutil
import sqlite3
import subprocess
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.config import Settings
from app.models import (
    AgentProviderConnectionStatus,
    AgentProviderAuthFlowResponse,
    AgentProviderMetadataResponse,
    AgentProviderPrimaryStatus,
    AgentProviderReadiness,
    AgentProviderStatus,
    AgentProviderStatusResponse,
    AgentProviderVerificationResponse,
)


class AgentProviderError(Exception):
    """
    Raised when an agent provider cannot complete a task.
    """


CODEX_CHATGPT_OAUTH_AUTH_MODE = "chatgpt"
CODEX_API_KEY_AUTH_MODE = "api_key"
CODEX_ACTIVE_AUTH_MODE = CODEX_CHATGPT_OAUTH_AUTH_MODE
CODEX_CONNECTED_AUTH_MODES = {
    CODEX_CHATGPT_OAUTH_AUTH_MODE,
    CODEX_API_KEY_AUTH_MODE,
}
CODEX_PENDING_AUTH_MODES = {CODEX_CHATGPT_OAUTH_AUTH_MODE}

logger = logging.getLogger(__name__)

_SECRET_REDACTION_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)((?:id_token|access_token|refresh_token)=)[^&\s]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s,;]+"),
]
_ANSI_ESCAPE_SEQUENCE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

_DEVICE_AUTH_PROCESSES: dict[str, subprocess.Popen[str]] = {}
_DEVICE_AUTH_STARTED_AT: dict[str, float] = {}
_CODEX_LOCAL_LOGIN_INSTRUCTIONS = [
    "Open a terminal on the backend machine.",
    "Run `codex login`.",
    "Complete the ChatGPT sign-in in the browser.",
    "Return to AisoFlow and tap Check connection.",
]
_CODEX_DEVICE_AUTH_INSTRUCTIONS = [
    "Open the verification link on this device or another browser.",
    "Sign in with your ChatGPT account and enter the displayed code if prompted.",
    "Return to AisoFlow and tap Check connection.",
]


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

        if not self.success and self.message.strip():
            messages.append(("error", f"Runner error: {self.message.strip()}"))

        if self.duration_seconds is not None:
            messages.append(("info", f"Runner duration: {self.duration_seconds:.2f}s."))

        if self.exit_code is not None:
            level = "info" if self.exit_code == 0 else "error"
            messages.append((level, f"Runner exit code: {self.exit_code}."))

        if self.stdout.strip():
            messages.append(("info", f"Runner stdout:\n{_redact_secret_text(self.stdout.strip())}"))

        if self.stderr.strip():
            level = "info" if self.success else "error"
            messages.append((level, f"Runner stderr:\n{_redact_secret_text(self.stderr.strip())}"))

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
            "AisoFlow development note: target-aware execution touched this "
            f"file for review. Scope={change_scope}. Prompt={safe_instruction}"
        )

        if extension == ".py":
            return f"# TODO(AisoFlow): {note}\n"

        if extension in {".ts", ".tsx", ".js", ".jsx"}:
            return f"// TODO(AisoFlow): {note}\n"

        if extension == ".css":
            return f"/* TODO(AisoFlow): {note} */\n"

        if extension == ".md":
            return f"\n> TODO(AisoFlow): {note}\n"

        return f"\n{note}\n"

    def _build_task_note(
        self,
        instruction: str,
        timestamp: str,
    ) -> str:
        return (
            "# AisoFlow Task Note\n\n"
            "This file was generated by the AisoFlow development provider.\n\n"
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

    def connect_with_api_key(self, provider_id: str, api_key: str) -> None:
        secret = api_key.strip()
        if not secret:
            raise AgentProviderError("API key is required.")

        fingerprint = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        connected_at = datetime.now(timezone.utc).isoformat()

        with self._connect() as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO agent_provider_credentials (
                        provider_id,
                        auth_mode,
                        secret_fingerprint,
                        connected_at
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(provider_id) DO UPDATE SET
                        auth_mode = excluded.auth_mode,
                        secret_fingerprint = excluded.secret_fingerprint,
                        connected_at = excluded.connected_at
                    """,
                    (
                        provider_id,
                        CODEX_API_KEY_AUTH_MODE,
                        fingerprint,
                        connected_at,
                    ),
                )
                self._connect_row(connection, provider_id, CODEX_API_KEY_AUTH_MODE, connected_at)

    def disconnect(self, provider_id: str) -> None:
        with self._connect() as connection:
            with connection:
                connection.execute(
                    "DELETE FROM agent_provider_connections WHERE provider_id = ?",
                    (provider_id,),
                )
                connection.execute(
                    "DELETE FROM agent_provider_credentials WHERE provider_id = ?",
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
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_provider_credentials (
                        provider_id TEXT PRIMARY KEY,
                        auth_mode TEXT NOT NULL,
                        secret_fingerprint TEXT NOT NULL,
                        connected_at TEXT NOT NULL
                    )
                    """
                )
                columns = {
                    str(row["name"])
                    for row in connection.execute(
                        "PRAGMA table_info(agent_provider_credentials)"
                    ).fetchall()
                }
                if "secret_value" in columns:
                    connection.execute(
                        """
                        CREATE TABLE agent_provider_credentials_new (
                            provider_id TEXT PRIMARY KEY,
                            auth_mode TEXT NOT NULL,
                            secret_fingerprint TEXT NOT NULL,
                            connected_at TEXT NOT NULL
                        )
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO agent_provider_credentials_new (
                            provider_id,
                            auth_mode,
                            secret_fingerprint,
                            connected_at
                        )
                        SELECT provider_id, auth_mode, secret_fingerprint, connected_at
                        FROM agent_provider_credentials
                        """
                    )
                    connection.execute("DROP TABLE agent_provider_credentials")
                    connection.execute(
                        """
                        ALTER TABLE agent_provider_credentials_new
                        RENAME TO agent_provider_credentials
                        """
                    )
                connection.execute(
                    """
                    DELETE FROM agent_provider_connections
                    WHERE provider_id = 'codex'
                    AND auth_mode NOT IN (?, ?)
                    """,
                    (
                        CODEX_CHATGPT_OAUTH_AUTH_MODE,
                        CODEX_API_KEY_AUTH_MODE,
                    ),
                )
        try:
            self.db_path.chmod(0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _connect_row(
        self,
        connection: sqlite3.Connection,
        provider_id: str,
        auth_mode: str,
        connected_at: str,
    ) -> None:
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
            (provider_id, auth_mode, connected_at),
        )


def provider_metadata(settings: Settings) -> list[AgentProviderMetadataResponse]:
    """
    Return user-facing provider metadata and simulated connection state.
    """

    connected_auth_modes = AgentProviderConnectionStore(settings).connected_auth_modes()
    codex_readiness = _codex_readiness(settings)
    codex_active = connected_auth_modes.get("codex") in CODEX_CONNECTED_AUTH_MODES
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
            auth_mode=connected_auth_modes.get("codex", CODEX_CHATGPT_OAUTH_AUTH_MODE),
            connection=(
                AgentProviderConnectionStatus.connected
                if codex_active
                else AgentProviderConnectionStatus.disconnected
            ),
            readiness=codex_readiness,
            description="Connect with ChatGPT or an OpenAI API key. Cloud execution is not enabled yet.",
            supports_chatgpt_login=True,
            supports_api_key=True,
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
) -> AgentProviderMetadataResponse | AgentProviderAuthFlowResponse:
    valid_provider_ids = {provider.id for provider in provider_metadata(settings)}

    if provider_id not in valid_provider_ids:
        raise AgentProviderError(f"Unsupported agent provider: {provider_id}")

    metadata = next(
        provider for provider in provider_metadata(settings) if provider.id == provider_id
    )

    if metadata.status == AgentProviderConnectionStatus.coming_soon:
        raise AgentProviderError(f"Agent provider is coming soon: {provider_id}")

    if provider_id == "codex":
        if auth_mode == CODEX_API_KEY_AUTH_MODE:
            return start_codex_api_key_flow(settings)

        return start_codex_account_flow(settings)

    if metadata.readiness != AgentProviderReadiness.ready:
        raise AgentProviderError(f"Agent provider is not ready: {provider_id}")

    next_auth_mode = (
        CODEX_ACTIVE_AUTH_MODE
        if provider_id == "codex"
        else auth_mode or metadata.auth_mode
    )

    AgentProviderConnectionStore(settings).connect(provider_id, next_auth_mode)
    return next(
        provider for provider in provider_metadata(settings) if provider.id == provider_id
    )


def start_codex_account_flow(settings: Settings) -> AgentProviderAuthFlowResponse:
    error = _codex_runtime_error(settings)
    if error:
        raise AgentProviderError(error)

    parsed = _start_codex_device_auth(settings)
    metadata = next(provider for provider in provider_metadata(settings) if provider.id == "codex")
    verification_url = parsed.get("verification_url")

    return AgentProviderAuthFlowResponse(
        provider_id="codex",
        status="waiting_for_auth",
        message=(
            "Open the Codex verification link, sign in with your ChatGPT account, "
            "then return to AisoFlow and tap Check connection."
        ),
        auth_url=verification_url,
        verification_url=verification_url,
        device_code=parsed.get("device_code"),
        user_code=parsed.get("user_code"),
        instructions=_CODEX_DEVICE_AUTH_INSTRUCTIONS,
        metadata=metadata,
    )


def start_codex_api_key_flow(settings: Settings) -> AgentProviderAuthFlowResponse:
    metadata = next(provider for provider in provider_metadata(settings) if provider.id == "codex")

    return AgentProviderAuthFlowResponse(
        provider_id="codex",
        status="auth_required",
        message="Enter your OpenAI API key to connect Codex.",
        instructions=[
            "Submit the key once; AisoFlow sends it directly to the backend.",
            "The frontend does not persist the raw key after submission.",
            "Codex is marked connected only after the backend accepts the key.",
        ],
        metadata=metadata,
    )


def connect_provider_api_key(
    settings: Settings,
    provider_id: str,
    api_key: str,
) -> AgentProviderMetadataResponse:
    if provider_id != "codex":
        raise AgentProviderError(f"API key auth is not supported for provider: {provider_id}")

    secret = api_key.strip()
    if len(secret) < 20:
        raise AgentProviderError("OpenAI API key is too short.")

    error = _codex_runtime_error(settings)
    if error:
        raise AgentProviderError(error)

    login_error = _codex_api_key_login_error(settings, secret)
    if login_error is not None:
        raise AgentProviderError(login_error)

    verification_error = _codex_verification_error(settings)
    if verification_error is not None:
        raise AgentProviderError(verification_error)

    AgentProviderConnectionStore(settings).connect_with_api_key(provider_id, secret)
    return next(provider for provider in provider_metadata(settings) if provider.id == provider_id)


def verify_provider_connection(
    settings: Settings,
    provider_id: str,
    auth_mode: str | None = None,
) -> AgentProviderVerificationResponse:
    if provider_id != "codex":
        return AgentProviderVerificationResponse(
            provider_id=provider_id,
            connected=connect_provider(settings, provider_id).connection
            == AgentProviderConnectionStatus.connected,
            message="Provider connection verified.",
            metadata=next(
                provider for provider in provider_metadata(settings) if provider.id == provider_id
            ),
        )

    error = _codex_verification_error(settings)
    if error is not None:
        AgentProviderConnectionStore(settings).disconnect("codex")
        return AgentProviderVerificationResponse(
            provider_id="codex",
            connected=False,
            message=error,
            metadata=next(
                provider for provider in provider_metadata(settings) if provider.id == "codex"
            ),
        )

    next_auth_mode = auth_mode or CODEX_CHATGPT_OAUTH_AUTH_MODE
    if next_auth_mode not in CODEX_CONNECTED_AUTH_MODES:
        next_auth_mode = CODEX_CHATGPT_OAUTH_AUTH_MODE

    AgentProviderConnectionStore(settings).connect("codex", next_auth_mode)
    return AgentProviderVerificationResponse(
        provider_id="codex",
        connected=True,
        message="Authorization detected.",
        metadata=next(provider for provider in provider_metadata(settings) if provider.id == "codex"),
    )


def disconnect_provider(
    settings: Settings,
    provider_id: str,
) -> AgentProviderMetadataResponse:
    valid_provider_ids = {provider.id for provider in provider_metadata(settings)}

    if provider_id not in valid_provider_ids:
        raise AgentProviderError(f"Unsupported agent provider: {provider_id}")

    AgentProviderConnectionStore(settings).disconnect(provider_id)
    if provider_id == "codex":
        _stop_codex_device_auth(provider_id)
        _codex_logout(settings)
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
        primary_status=_primary_status(provider_id, connection, readiness),
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
                "Runs Codex inside the task workspace when enabled."
                if codex_ready
                else "Requires CODEX_ENABLED=true and the Codex CLI."
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


def _primary_status(
    provider_id: str,
    connection: AgentProviderConnectionStatus,
    readiness: AgentProviderReadiness,
) -> AgentProviderPrimaryStatus:
    if connection == AgentProviderConnectionStatus.connected:
        if provider_id == "codex":
            return AgentProviderPrimaryStatus.active

        return AgentProviderPrimaryStatus.connected

    if connection == AgentProviderConnectionStatus.connecting:
        return AgentProviderPrimaryStatus.connecting

    if readiness == AgentProviderReadiness.unavailable:
        return AgentProviderPrimaryStatus.unavailable

    if readiness == AgentProviderReadiness.not_configured:
        return AgentProviderPrimaryStatus.not_configured

    return AgentProviderPrimaryStatus.disconnected


def _codex_readiness(settings: Settings) -> AgentProviderReadiness:
    connected = AgentProviderConnectionStore(settings).connected_auth_modes().get("codex")
    if connected in CODEX_CONNECTED_AUTH_MODES:
        return AgentProviderReadiness.ready

    return AgentProviderReadiness.auth_required


def _codex_cli_ready(settings: Settings) -> bool:
    return settings.codex_enabled and _codex_cli_available(settings)


def _codex_cli_available(settings: Settings) -> bool:
    try:
        command = shlex.split(settings.codex_cli_command)
    except ValueError:
        return False

    return bool(command and shutil.which(command[0]))


def _codex_command(settings: Settings) -> list[str] | str:
    try:
        command = shlex.split(settings.codex_cli_command)
    except ValueError as exc:
        return f"Invalid CODEX_CLI_COMMAND: {exc}"

    if not command:
        return "CODEX_CLI_COMMAND cannot be empty."

    if not shutil.which(command[0]):
        return "Codex CLI is not available from CODEX_CLI_COMMAND."

    return command


def _codex_runtime_error(settings: Settings) -> str | None:
    if not settings.codex_enabled:
        return "Codex is unavailable."

    command = _codex_command(settings)
    if isinstance(command, str):
        return command

    return None


def _start_codex_device_auth(settings: Settings) -> dict[str, str]:
    command = _codex_command(settings)
    if isinstance(command, str):
        raise AgentProviderError(command)

    _stop_codex_device_auth("codex")
    settings.workspace_root.mkdir(parents=True, exist_ok=True)

    try:
        process = subprocess.Popen(
            [*command, "login", "--device-auth"],
            cwd=settings.workspace_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        raise AgentProviderError(f"Codex authentication failed to start: {exc}") from exc

    _DEVICE_AUTH_PROCESSES["codex"] = process
    _DEVICE_AUTH_STARTED_AT["codex"] = time.monotonic()
    lines = _read_initial_process_output(process, timeout_seconds=8)
    output = _strip_ansi_escape_sequences("\n".join(lines))
    parsed = parse_codex_device_auth_output(output)

    if process.poll() is not None and process.returncode not in (0, None):
        output = _redact_secret_text(output).strip()
        raise AgentProviderError(
            f"Codex authentication failed to start. {output}".strip()
        )

    if not parsed.get("verification_url"):
        output = _redact_secret_text(output).strip() or "<no output>"
        logger.warning("Unable to parse Codex device authentication output: %s", output)
        raise AgentProviderError(
            "Codex did not return a device authentication URL. "
            f"Update the Codex CLI and try again. CLI output: {output}"
        )

    return parsed


def _read_initial_process_output(
    process: subprocess.Popen[str],
    timeout_seconds: float,
) -> list[str]:
    output_queue: queue.Queue[str | None] = queue.Queue()

    def reader() -> None:
        if process.stdout is None:
            output_queue.put(None)
            return

        for line in process.stdout:
            output_queue.put(line.rstrip("\n"))

        output_queue.put(None)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout_seconds
    lines: list[str] = []

    while time.monotonic() < deadline:
        try:
            item = output_queue.get(timeout=0.1)
        except queue.Empty:
            if process.poll() is not None:
                break
            continue

        if item is None:
            break

        lines.append(_strip_ansi_escape_sequences(item))
        parsed = parse_codex_device_auth_output("\n".join(lines))
        if parsed.get("verification_url") and (
            parsed.get("user_code") or parsed.get("device_code")
        ):
            break

    return lines


def parse_codex_device_auth_output(output: str) -> dict[str, str]:
    output = _strip_ansi_escape_sequences(output)
    verification_url = _first_match(
        output,
        [
            r"(?im)^\s*(?:visit|open)\s*:\s*(https://[^\s'\"<>]+)",
            r"https://[^\s'\"<>]+",
        ],
    )
    user_code = _first_match(
        output,
        [
            r"(?i)\buser\s+code\s*[:=]\s*([A-Z0-9][A-Z0-9\-]{3,})",
            r"(?i)\benter\s+(?:the\s+)?code\s*[:=]?\s*([A-Z0-9][A-Z0-9\-]{3,})",
            r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b",
        ],
    )
    device_code = _first_match(
        output,
        [
            r"(?i)device\s+code\s*[:=]\s*([A-Za-z0-9._\-]+)",
        ],
    )

    parsed: dict[str, str] = {}
    if verification_url:
        parsed["verification_url"] = _strip_token_query_params(
            verification_url.rstrip(".,)")
        )
    if user_code:
        parsed["user_code"] = user_code
    if device_code:
        parsed["device_code"] = device_code

    return parsed


def _strip_ansi_escape_sequences(output: str) -> str:
    return _ANSI_ESCAPE_SEQUENCE_PATTERN.sub("", output)


def _first_match(output: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, output)
        if match:
            return match.group(1) if match.lastindex else match.group(0)

    return None


def _strip_token_query_params(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return _redact_secret_text(url)

    sensitive = {"id_token", "access_token", "refresh_token"}
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in sensitive
        ],
        doseq=True,
    )
    fragment = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.fragment, keep_blank_values=True)
            if key.lower() not in sensitive
        ],
        doseq=True,
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, fragment))


def _codex_api_key_login_error(settings: Settings, api_key: str) -> str | None:
    command = _codex_command(settings)
    if isinstance(command, str):
        return command

    settings.workspace_root.mkdir(parents=True, exist_ok=True)

    try:
        completed = subprocess.run(
            [*command, "login", "--with-api-key"],
            cwd=settings.workspace_root,
            input=f"{api_key}\n",
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "Codex API key authentication timed out."
    except OSError as exc:
        return f"Codex API key authentication failed to start: {exc}"

    if completed.returncode != 0:
        summary = _redact_secret_text(
            completed.stderr or completed.stdout or "Codex API key authentication failed."
        ).strip().splitlines()
        suffix = f" {summary[0]}" if summary else ""
        return f"Codex API key authentication failed.{suffix}".strip()

    return None


def _codex_verification_error(settings: Settings) -> str | None:
    runtime_error = _codex_runtime_error(settings)
    if runtime_error:
        return runtime_error

    pending_error = _codex_pending_auth_error(settings)
    if pending_error:
        return pending_error

    command = _codex_command(settings)
    if isinstance(command, str):
        return command

    workspace_root = settings.workspace_root
    workspace_root.mkdir(parents=True, exist_ok=True)
    timeout_seconds = min(settings.codex_timeout_seconds, 30)
    _log_codex_verification_context(workspace_root)

    status_result = _run_codex_check(
        [*command, "login", "status"],
        workspace_root,
        timeout_seconds,
    )
    if status_result is None:
        return "Codex verification timed out."

    if status_result.returncode == 0:
        _finish_codex_auth("codex")
        return None

    status_output = status_result.stderr or status_result.stdout or ""
    if status_output and not _codex_status_unsupported(status_output):
        summary = _redact_secret_text(status_output).strip().splitlines()
        suffix = f" {summary[0]}" if summary else ""
        return f"Codex verification failed.{suffix}".strip()

    exec_result = _run_codex_check(
        [*command, "exec", "Say OK"],
        workspace_root,
        timeout_seconds,
    )
    if exec_result is None:
        return "Codex verification timed out."

    if exec_result.returncode != 0:
        summary = _redact_secret_text(
            exec_result.stderr or exec_result.stdout or ""
        ).strip().splitlines()
        suffix = f" {summary[0]}" if summary else ""
        return f"Codex verification failed.{suffix}".strip()

    _finish_codex_auth("codex")
    return None


def _codex_pending_auth_error(settings: Settings) -> str | None:
    process = _DEVICE_AUTH_PROCESSES.get("codex")
    if process is None:
        return None

    started_at = _DEVICE_AUTH_STARTED_AT.get("codex", time.monotonic())
    if process.poll() is None:
        if time.monotonic() - started_at > settings.codex_timeout_seconds:
            _stop_codex_device_auth("codex")
            return "Codex authentication timed out."
        return None

    _clear_finished_codex_auth("codex")
    if process.returncode not in (0, None):
        return "Codex authentication process exited before authorization completed."

    return None


def _clear_finished_codex_auth(provider_id: str) -> None:
    process = _DEVICE_AUTH_PROCESSES.get(provider_id)
    if process is not None and process.poll() is None:
        return

    _DEVICE_AUTH_PROCESSES.pop(provider_id, None)
    _DEVICE_AUTH_STARTED_AT.pop(provider_id, None)


def _finish_codex_auth(provider_id: str) -> None:
    process = _DEVICE_AUTH_PROCESSES.get(provider_id)
    if process is not None and process.poll() is None:
        _stop_codex_device_auth(provider_id)
        return

    _clear_finished_codex_auth(provider_id)


def _log_codex_verification_context(cwd: Path) -> None:
    home = Path(os.environ.get("HOME", "")).expanduser()
    codex_home = Path(os.environ.get("CODEX_HOME", str(home / ".codex"))).expanduser()
    logger.info(
        "Verifying Codex CLI session: HOME=%s USER=%s cwd=%s codex_home=%s auth_path=%s config_path=%s",
        str(home) if str(home) else "",
        os.environ.get("USER", ""),
        str(cwd),
        str(codex_home),
        str(codex_home / "auth.json"),
        str(codex_home / "config.toml"),
    )


def _run_codex_check(
    args: list[str],
    cwd: Path,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    except OSError as exc:
        return subprocess.CompletedProcess(args=args, returncode=127, stdout="", stderr=str(exc))


def _codex_status_unsupported(output: str) -> bool:
    lowered = output.lower()
    return any(
        marker in lowered
        for marker in (
            "unknown command",
            "unrecognized subcommand",
            "unexpected argument",
            "invalid command",
            "usage:",
        )
    )


def _stop_codex_device_auth(provider_id: str) -> None:
    process = _DEVICE_AUTH_PROCESSES.pop(provider_id, None)
    _DEVICE_AUTH_STARTED_AT.pop(provider_id, None)
    if process is None or process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()


def _codex_logout(settings: Settings) -> None:
    if not settings.codex_enabled:
        return

    command = _codex_command(settings)
    if isinstance(command, str):
        return

    settings.workspace_root.mkdir(parents=True, exist_ok=True)

    try:
        subprocess.run(
            [*command, "logout"],
            cwd=settings.workspace_root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return


def _redact_secret_text(value: str) -> str:
    redacted = value
    for pattern in _SECRET_REDACTION_PATTERNS:
        redacted = pattern.sub(r"\1[REDACTED]" if pattern.groups else "[REDACTED]", redacted)
    return redacted


def _codex_required_config(
    settings: Settings,
    codex_cli_available: bool,
) -> list[str]:
    required: list[str] = []

    if not settings.codex_enabled:
        required.append("CODEX_ENABLED")

    if not codex_cli_available:
        required.append("CODEX_CLI_COMMAND")

    return required
