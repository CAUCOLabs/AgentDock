from dataclasses import dataclass
import logging

from app.agent_providers import AgentProviderError
from app.agent_runner import AgentRunnerError, get_agent_runner
from app.config import Settings
from app.diff_service import DiffService, DiffServiceError
from app.github_client import GitHubClient, GitHubClientError
from app.models import AgentProvider, ChangedFile
from app.workspace import WorkspaceError, WorkspaceManager

logger = logging.getLogger(__name__)


class TaskRunnerError(Exception):
    """
    Raised when a task runner operation fails.
    """

    def __init__(
        self,
        message: str,
        log_messages: list[tuple[str, str]] | None = None,
        error_type: str | None = None,
        provider: AgentProvider | str | None = None,
    ) -> None:
        super().__init__(message)
        self.log_messages = log_messages or []
        self.error_type = error_type or self.__class__.__name__
        self.provider = provider.value if isinstance(provider, AgentProvider) else provider


@dataclass
class TaskRunResult:
    """
    Result produced by running an AgentDock task.
    """

    diff: str
    changed_files: list[ChangedFile]
    message: str
    log_messages: list[tuple[str, str]]


class TaskRunner:
    """
    Orchestrates the full task execution workflow.

    Current MVP flow:

    - create or ensure task workspace
    - clone repository into workspace
    - create a safe working branch
    - run selected agent provider
    - generate Git diff
    - return changed files for review
    """

    def __init__(self, settings: Settings, github_token: str | None = None) -> None:
        self.settings = settings
        self.workspace_manager = WorkspaceManager(settings)
        self.github_client = GitHubClient(settings, github_token=github_token)

    def run_task(
        self,
        task_id: str,
        repo_url: str,
        base_branch: str,
        working_branch: str,
        instruction: str,
        agent: AgentProvider,
        target_file: str | None = None,
        change_scope: str = "only_selected_file",
    ) -> TaskRunResult:
        """
        Run a task and return the generated review data.
        """

        try:
            repo_path = self.workspace_manager.repo_path(task_id)

            repo = self.github_client.clone_repository(
                repo_url=repo_url,
                destination=repo_path,
                branch=base_branch,
            )

            self.github_client.create_working_branch(
                repo=repo,
                working_branch=working_branch,
            )

            runner = get_agent_runner(agent, self.settings)

            agent_result = runner.run(
                repo_path=repo_path,
                instruction=instruction,
                target_file=target_file,
                change_scope=change_scope,
            )

            if not agent_result.success:
                raise TaskRunnerError(
                    agent_result.message,
                    log_messages=agent_result.log_messages(),
                    error_type="ProviderExecutionError",
                    provider=agent,
                )

            diff_service = DiffService(repo_path)

            diff = diff_service.get_diff()
            changed_files = diff_service.get_changed_files()

            if not diff_service.has_changes():
                raise TaskRunnerError(
                    "Agent completed but produced no changes.",
                    log_messages=agent_result.log_messages(),
                    error_type="ProviderNoChangesError",
                    provider=agent,
                )

            return TaskRunResult(
                diff=diff,
                changed_files=changed_files,
                message=agent_result.message,
                log_messages=agent_result.log_messages(),
            )

        except (
            WorkspaceError,
            GitHubClientError,
            AgentProviderError,
            AgentRunnerError,
            DiffServiceError,
        ) as exc:
            logger.exception(
                "Task runner failed for task_id=%s provider=%s",
                task_id,
                agent.value,
            )
            raise TaskRunnerError(
                str(exc),
                log_messages=[("error", f"Task runner error: {exc}")],
                error_type=exc.__class__.__name__,
                provider=agent,
            ) from exc
        except TaskRunnerError:
            logger.exception(
                "Task runner failed for task_id=%s provider=%s",
                task_id,
                agent.value,
            )
            raise
        except Exception as exc:
            logger.exception(
                "Unexpected task runner failure for task_id=%s provider=%s",
                task_id,
                agent.value,
            )
            raise TaskRunnerError(
                str(exc),
                log_messages=[("error", f"Unexpected task runner error: {exc}")],
                error_type=exc.__class__.__name__,
                provider=agent,
            ) from exc

    def cleanup_task_workspace(self, task_id: str) -> None:
        """
        Delete a task workspace.

        This will be useful later for cancelled/rejected tasks or cleanup jobs.
        """

        try:
            self.workspace_manager.delete_workspace(task_id)
        except WorkspaceError as exc:
            raise TaskRunnerError(str(exc)) from exc
