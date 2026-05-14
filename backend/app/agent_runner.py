from pathlib import Path
import shlex
import shutil
import subprocess
from time import perf_counter

from app.agent_providers import (
    AgentProviderBase,
    AgentProviderConnectionStore,
    AgentProviderError,
    AgentRunResult,
    MockProvider,
)
from app.config import Settings
from app.models import AgentProvider


class AgentRunnerError(Exception):
    """
    Raised when an agent runner operation fails.
    """


class OpenAIAgentRunner(AgentProviderBase):
    """
    Placeholder for future OpenAI-powered coding agent integration.
    """

    def run(
        self,
        repo_path: Path,
        instruction: str,
        target_file: str | None = None,
        change_scope: str = "only_selected_file",
    ) -> AgentRunResult:
        """
        Future implementation.

        For now we keep this disabled so the MVP remains deterministic
        and safe while the Git workflow is being built.
        """

        raise AgentRunnerError(
            "OpenAI agent runner is not implemented yet."
        )


class LocalRunner(AgentProviderBase):
    """
    Runs a configured local command inside the isolated task repository.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(
        self,
        repo_path: Path,
        instruction: str,
        target_file: str | None = None,
        change_scope: str = "only_selected_file",
    ) -> AgentRunResult:
        """
        Execute the configured command and capture process details.
        """

        command = (self.settings.local_runner_command or "").strip()

        if not command:
            raise AgentRunnerError("LOCAL_RUNNER_COMMAND is required for local runner.")

        safe_repo_path = self._validate_repo_path(repo_path)
        timeout_seconds = (
            self.settings.local_runner_timeout_seconds
            or self.settings.max_task_runtime_seconds
        )

        try:
            args = shlex.split(command)
        except ValueError as exc:
            raise AgentRunnerError(f"Invalid LOCAL_RUNNER_COMMAND: {exc}") from exc

        if not args:
            raise AgentRunnerError("LOCAL_RUNNER_COMMAND cannot be empty.")

        started_at = perf_counter()

        try:
            completed = subprocess.run(
                args,
                cwd=safe_repo_path,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration_seconds = perf_counter() - started_at
            stdout = self._process_output(exc.stdout)
            stderr = self._process_output(exc.stderr)

            return AgentRunResult(
                success=False,
                message=f"Local runner timed out after {timeout_seconds}s.",
                stdout=stdout,
                stderr=stderr,
                exit_code=None,
                duration_seconds=duration_seconds,
            )
        except OSError as exc:
            duration_seconds = perf_counter() - started_at

            return AgentRunResult(
                success=False,
                message=f"Local runner failed to start: {exc}",
                stderr=str(exc),
                exit_code=None,
                duration_seconds=duration_seconds,
            )

        duration_seconds = perf_counter() - started_at
        success = completed.returncode == 0

        return AgentRunResult(
            success=success,
            message=(
                "Local runner completed successfully."
                if success
                else "Local runner failed."
            ),
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
            duration_seconds=duration_seconds,
        )

    def _validate_repo_path(self, repo_path: Path) -> Path:
        """
        Keep local commands constrained to AgentDock task workspaces.
        """

        workspace_root = self.settings.workspace_root.resolve()
        resolved_repo_path = repo_path.resolve()

        try:
            resolved_repo_path.relative_to(workspace_root)
        except ValueError as exc:
            raise AgentRunnerError(
                f"Local runner repository path is outside workspace root: {repo_path}"
            ) from exc

        if resolved_repo_path.name != "repo":
            raise AgentRunnerError(
                f"Local runner must execute in a task repo directory: {repo_path}"
            )

        if not (resolved_repo_path / ".git").exists():
            raise AgentRunnerError(f"Local runner path is not a Git repo: {repo_path}")

        return resolved_repo_path

    def _process_output(self, value: str | bytes | None) -> str:
        if value is None:
            return ""

        if isinstance(value, bytes):
            return value.decode(errors="replace")

        return value


class CodexCliProvider(AgentProviderBase):
    """
    Runs the local Codex CLI inside an isolated task repository.
    """

    AUTH_CHECK_TIMEOUT_SECONDS = 5

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(
        self,
        repo_path: Path,
        instruction: str,
        target_file: str | None = None,
        change_scope: str = "only_selected_file",
    ) -> AgentRunResult:
        if not self.is_ready(self.settings):
            return MockProvider().run(
                repo_path=repo_path,
                instruction=instruction,
                target_file=target_file,
                change_scope=change_scope,
            )

        safe_repo_path = self._validate_repo_path(repo_path)
        prompt = self._build_prompt(
            instruction=instruction,
            target_file=target_file,
            change_scope=change_scope,
        )
        args = self._command_args(prompt)
        started_at = perf_counter()

        try:
            completed = subprocess.run(
                args,
                cwd=safe_repo_path,
                capture_output=True,
                text=True,
                timeout=self.settings.codex_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration_seconds = perf_counter() - started_at
            return AgentRunResult(
                success=False,
                message=(
                    f"Codex CLI timed out after "
                    f"{self.settings.codex_timeout_seconds}s."
                ),
                stdout=self._process_output(exc.stdout),
                stderr=self._process_output(exc.stderr),
                exit_code=None,
                duration_seconds=duration_seconds,
            )
        except OSError as exc:
            duration_seconds = perf_counter() - started_at
            return AgentRunResult(
                success=False,
                message=f"Codex CLI failed to start: {exc}",
                stderr=str(exc),
                exit_code=None,
                duration_seconds=duration_seconds,
            )

        duration_seconds = perf_counter() - started_at
        success = completed.returncode == 0

        return AgentRunResult(
            success=success,
            message=(
                "Codex CLI completed successfully."
                if success
                else "Codex CLI failed."
            ),
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
            duration_seconds=duration_seconds,
        )

    @classmethod
    def is_ready(cls, settings: Settings) -> bool:
        return (
            settings.codex_enabled
            and cls.cli_available(settings)
            and cls.is_authenticated(settings)
        )

    @classmethod
    def cli_available(cls, settings: Settings) -> bool:
        try:
            args = shlex.split(settings.codex_cli_command)
        except ValueError:
            return False

        return bool(args and shutil.which(args[0]))

    @classmethod
    def is_authenticated(cls, settings: Settings) -> bool:
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
                timeout=cls.AUTH_CHECK_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False

        return completed.returncode == 0

    def _command_args(self, prompt: str) -> list[str]:
        try:
            command = shlex.split(self.settings.codex_cli_command)
        except ValueError as exc:
            raise AgentRunnerError(f"Invalid CODEX_CLI_COMMAND: {exc}") from exc

        if not command:
            raise AgentRunnerError("CODEX_CLI_COMMAND cannot be empty.")

        return [
            *command,
            "exec",
            "--sandbox",
            "workspace-write",
            "--ask-for-approval",
            "never",
            prompt,
        ]

    def _validate_repo_path(self, repo_path: Path) -> Path:
        workspace_root = self.settings.workspace_root.resolve()
        resolved_repo_path = repo_path.resolve()

        try:
            resolved_repo_path.relative_to(workspace_root)
        except ValueError as exc:
            raise AgentRunnerError(
                f"Codex CLI repository path is outside workspace root: {repo_path}"
            ) from exc

        if resolved_repo_path.name != "repo":
            raise AgentRunnerError(
                f"Codex CLI must execute in a task repo directory: {repo_path}"
            )

        if not (resolved_repo_path / ".git").exists():
            raise AgentRunnerError(f"Codex CLI path is not a Git repo: {repo_path}")

        return resolved_repo_path

    def _build_prompt(
        self,
        instruction: str,
        target_file: str | None,
        change_scope: str,
    ) -> str:
        target_context = (
            f"Selected target file: {target_file}"
            if target_file
            else "Selected target file: none"
        )
        scope_instruction = (
            "Change scope: only_selected_file. Modify only the selected target "
            "file. Do not create, edit, delete, format, or rename any other "
            "file."
            if change_scope == "only_selected_file"
            else "Change scope: allow_related_files. Keep related file changes minimal and necessary."
        )

        return (
            "You are running inside an AgentDock task workspace.\n"
            "Leave all changes as working tree edits for git diff review.\n"
            "Do not commit, push, create branches, open pull requests, or run destructive git commands.\n\n"
            f"{target_context}\n"
            f"{scope_instruction}\n\n"
            "User instruction:\n"
            f"{instruction.strip()}\n"
        )

    def _process_output(self, value: str | bytes | None) -> str:
        if value is None:
            return ""

        if isinstance(value, bytes):
            return value.decode(errors="replace")

        return value


def get_agent_runner(provider: AgentProvider, settings: Settings) -> AgentProviderBase:
    """
    Return the correct runner for the selected provider.
    """

    if _should_use_local_runner(provider, settings):
        return LocalRunner(settings)

    if (
        provider == AgentProvider.codex
        and CodexCliProvider.is_ready(settings)
        and "codex" in AgentProviderConnectionStore(settings).connected_auth_modes()
    ):
        return CodexCliProvider(settings)

    if provider in {
        AgentProvider.mock,
        AgentProvider.codex,
        AgentProvider.claude,
        AgentProvider.ollama,
        AgentProvider.local_model,
    }:
        return MockProvider()

    if provider == AgentProvider.openai:
        return OpenAIAgentRunner()

    raise AgentRunnerError(f"Unsupported agent provider: {provider}")


def _should_use_local_runner(provider: AgentProvider, settings: Settings) -> bool:
    configured_runner = settings.agent_runner.lower().strip()
    configured_provider = settings.ai_provider.lower().strip()
    has_local_command = bool(settings.local_runner_command)

    if provider.value == "local":
        return True

    return (
        provider in {AgentProvider.mock, AgentProvider.local_model}
        and configured_runner == "local"
        and has_local_command
    ) or (configured_provider == "local" and has_local_command)


BaseAgentRunner = AgentProviderBase
MockAgentRunner = MockProvider
LocalAgentRunner = LocalRunner
