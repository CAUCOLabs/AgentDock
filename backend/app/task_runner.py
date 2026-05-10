from dataclasses import dataclass

from app.agent_runner import AgentRunnerError, get_agent_runner
from app.config import Settings
from app.diff_service import DiffService, DiffServiceError
from app.github_client import GitHubClient, GitHubClientError
from app.models import AgentProvider, ChangedFile
from app.workspace import WorkspaceError, WorkspaceManager


class TaskRunnerError(Exception):
    """
    Raised when a task runner operation fails.
    """


@dataclass
class TaskRunResult:
    """
    Result produced by running an AgentDock task.
    """

    diff: str
    changed_files: list[ChangedFile]
    message: str


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

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.workspace_manager = WorkspaceManager(settings)
        self.github_client = GitHubClient(settings)

    def run_task(
        self,
        task_id: str,
        repo_url: str,
        base_branch: str,
        working_branch: str,
        instruction: str,
        agent: AgentProvider,
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

            runner = get_agent_runner(agent)

            agent_result = runner.run(
                repo_path=repo_path,
                instruction=instruction,
            )

            if not agent_result.success:
                raise TaskRunnerError(agent_result.message)

            diff_service = DiffService(repo_path)

            diff = diff_service.get_diff()
            changed_files = diff_service.get_changed_files()

            if not diff_service.has_changes():
                raise TaskRunnerError("Agent completed but produced no changes.")

            return TaskRunResult(
                diff=diff,
                changed_files=changed_files,
                message=agent_result.message,
            )

        except (
            WorkspaceError,
            GitHubClientError,
            AgentRunnerError,
            DiffServiceError,
        ) as exc:
            raise TaskRunnerError(str(exc)) from exc

    def cleanup_task_workspace(self, task_id: str) -> None:
        """
        Delete a task workspace.

        This will be useful later for cancelled/rejected tasks or cleanup jobs.
        """

        try:
            self.workspace_manager.delete_workspace(task_id)
        except WorkspaceError as exc:
            raise TaskRunnerError(str(exc)) from exc