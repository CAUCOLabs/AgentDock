from pathlib import Path
import shutil

from app.config import Settings
from app.models import create_task_id


class WorkspaceError(Exception):
    """
    Raised when a workspace operation fails.
    """


class WorkspaceManager:
    """
    Manages local isolated workspaces for AgentDock tasks.

    Each task gets its own folder under the configured workspaces directory.
    The repository will later be cloned inside that folder.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.workspace_root
        self.root.mkdir(parents=True, exist_ok=True)

    def create_workspace(self, task_id: str | None = None) -> Path:
        """
        Create and return a workspace path for a task.
        """

        safe_task_id = task_id or create_task_id()
        self._validate_task_id(safe_task_id)

        workspace_path = self.root / safe_task_id

        if workspace_path.exists():
            raise WorkspaceError(f"Workspace already exists: {workspace_path}")

        workspace_path.mkdir(parents=True, exist_ok=False)
        return workspace_path

    def get_workspace(self, task_id: str) -> Path:
        """
        Return the workspace path for a task.
        """

        self._validate_task_id(task_id)
        return self.root / task_id

    def delete_workspace(self, task_id: str) -> None:
        """
        Delete a task workspace if it exists.
        """

        workspace_path = self.get_workspace(task_id)

        if not workspace_path.exists():
            return

        if not workspace_path.is_dir():
            raise WorkspaceError(f"Workspace path is not a directory: {workspace_path}")

        shutil.rmtree(workspace_path)

    def ensure_workspace(self, task_id: str) -> Path:
        """
        Return an existing workspace or create it if missing.
        """

        workspace_path = self.get_workspace(task_id)

        if workspace_path.exists():
            if not workspace_path.is_dir():
                raise WorkspaceError(
                    f"Workspace path exists but is not a directory: {workspace_path}"
                )
            return workspace_path

        workspace_path.mkdir(parents=True, exist_ok=True)
        return workspace_path

    def repo_path(self, task_id: str) -> Path:
        """
        Return the expected repository path inside a task workspace.
        """

        return self.ensure_workspace(task_id) / "repo"

    def _validate_task_id(self, task_id: str) -> None:
        """
        Prevent path traversal or unsafe workspace names.
        """

        if not task_id:
            raise WorkspaceError("Task id cannot be empty.")

        if "/" in task_id or "\\" in task_id or ".." in task_id:
            raise WorkspaceError(f"Unsafe task id: {task_id}")