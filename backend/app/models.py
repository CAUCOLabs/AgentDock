from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl


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
    openai = "openai"


class TaskCreateRequest(BaseModel):
    """
    Request body for creating a new coding task.
    """

    repo_url: HttpUrl = Field(
        ...,
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
    base_branch: str
    working_branch: str | None = None
    instruction: str
    agent: AgentProvider
    status: TaskStatus
    changed_files: list[ChangedFile] = Field(default_factory=list)
    commit_sha: str | None = None
    pushed: bool = False
    pr_url: str | None = None
    created_at: datetime
    updated_at: datetime
    error: str | None = None


class TaskDiffResponse(BaseModel):
    """
    Diff response for reviewing generated changes.
    """

    task_id: str
    status: TaskStatus
    diff: str
    changed_files: list[ChangedFile] = Field(default_factory=list)


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
