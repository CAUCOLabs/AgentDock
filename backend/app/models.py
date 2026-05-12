from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """
    Lifecycle states for an AgentDock task.
    """

    queued = "queued"
    running = "running"
    waiting_for_approval = "waiting_for_approval"
    approved = "approved"
    rejected = "rejected"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class AgentProvider(str, Enum):
    """
    Supported AI agent providers.

    For now, we start with mock. Real providers will be added later.
    """

    mock = "mock"
    local = "local"
    openai = "openai"


class TaskCreateRequest(BaseModel):
    """
    Request body for creating a new coding task.
    """

    repo_url: str = Field(
        ...,
        min_length=1,
        description="GitHub repository URL.",
        examples=["https://github.com/example/project"],
    )
    base_branch: str = Field(
        default="main",
        description="Base branch used to create the working branch.",
        examples=["main"],
    )
    instruction: str = Field(
        ...,
        min_length=10,
        description="Natural language task instruction for the coding agent.",
        examples=[
            "Update the reports page to consume the new reporting endpoints. Keep the change minimal."
        ],
    )
    agent: AgentProvider = Field(
        default=AgentProvider.mock,
        description="AI agent provider to use for this task.",
    )
    target_file: str | None = Field(
        default=None,
        description="Optional selected repository file path for task context.",
    )


class TaskCreateResponse(BaseModel):
    """
    Response returned after a task is created.
    """

    task_id: str
    status: TaskStatus
    message: str


class ChangedFile(BaseModel):
    """
    A file changed by a task.
    """

    path: str
    status: str = Field(
        description="Git change status, such as modified, added, deleted, or renamed."
    )
    additions: int = 0
    deletions: int = 0


class TaskSummary(BaseModel):
    """
    Public summary of a task.
    """

    task_id: str
    repo_url: str
    repo_owner: str | None = None
    repo_name: str | None = None
    base_branch: str
    working_branch: str | None = None
    workspace_path: str | None = None
    target_file: str | None = None
    instruction: str
    agent: AgentProvider
    status: TaskStatus
    changed_files: list[ChangedFile] = Field(default_factory=list)
    commit_sha: str | None = None
    pushed: bool = False
    pushed_at: datetime | None = None
    remote_branch: str | None = None
    last_push_result: str | None = None
    pr_url: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    rejected_at: datetime | None = None
    deleted_at: datetime | None = None
    error: str | None = None


class TaskDiffResponse(BaseModel):
    """
    Diff response for reviewing generated changes.
    """

    task_id: str
    status: TaskStatus
    diff: str
    changed_files: list[ChangedFile] = Field(default_factory=list)


class RepositoryFileEntry(BaseModel):
    """
    Read-only repository file tree entry.
    """

    path: str
    type: str
    size: int | None = None
    extension: str | None = None


class RepositoryFilesRequest(BaseModel):
    """
    Request used to inspect repository files.
    """

    repo_url: str = Field(..., min_length=1)
    base_branch: str = "main"


class RepositoryFilesResponse(BaseModel):
    """
    Repository file listing response.
    """

    repo_url: str
    base_branch: str
    files: list[RepositoryFileEntry] = Field(default_factory=list)


class RepositoryFilePreviewRequest(RepositoryFilesRequest):
    """
    Request used to preview a single repository file.
    """

    path: str = Field(..., min_length=1)


class RepositoryFilePreviewResponse(BaseModel):
    """
    Read-only bounded text preview for a repository file.
    """

    repo_url: str
    base_branch: str
    path: str
    size: int
    truncated: bool = False
    content: str


class WorkspaceStatusResponse(BaseModel):
    """
    Current Git workspace status for a task repository.
    """

    task_id: str
    repo_path: str
    branch: str | None = None
    head_commit: str | None = None
    remote_url: str | None = None
    has_valid_origin: bool = False
    default_branch: str | None = None
    push_enabled: bool = False
    pr_creation_enabled: bool = False
    is_dirty: bool
    has_untracked_files: bool
    changed_files: list[ChangedFile] = Field(default_factory=list)
    commit_sha: str | None = None
    pushed: bool = False
    pushed_at: datetime | None = None
    remote_branch: str | None = None
    last_push_result: str | None = None
    pr_url: str | None = None


class TaskLogEvent(BaseModel):
    """
    A timestamped event in a task lifecycle.
    """

    timestamp: datetime
    level: str
    message: str


class TaskLogsResponse(BaseModel):
    """
    Response returned with task event logs.
    """

    task_id: str
    events: list[TaskLogEvent] = Field(default_factory=list)


class TaskDeleteResponse(BaseModel):
    """
    Response returned after deleting a task and its workspace.
    """

    task_id: str
    status: str
    message: str


class TaskApprovalRequest(BaseModel):
    """
    Request used to approve or reject a task.
    """

    approve: bool
    message: str | None = Field(
        default=None,
        description="Optional approval or rejection note.",
    )


class TaskApprovalResponse(BaseModel):
    """
    Response returned after approving or rejecting a task.
    """

    task_id: str
    status: TaskStatus
    message: str


class ErrorResponse(BaseModel):
    """
    Standard API error response.
    """

    error: str
    details: dict[str, Any] | None = None


def create_task_id() -> str:
    """
    Create a stable task identifier.
    """

    return f"task_{uuid4().hex[:12]}"


def create_working_branch(task_id: str) -> str:
    """
    Create the default safe working branch name for a task.
    """

    return f"agentdock/{task_id}"
