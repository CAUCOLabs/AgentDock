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
    committed = "committed"
    pushed = "pushed"
    pull_request_opened = "pull_request_opened"
    rejected = "rejected"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class AgentProvider(str, Enum):
    """
    Supported AI agent providers.

    Legacy values remain accepted for existing clients and persisted tasks.
    New visible provider choices are scaffolded and route through the
    deterministic development provider until real integrations are added.
    """

    mock = "mock"
    local = "local"
    openai = "openai"
    codex = "codex"
    claude = "claude"
    ollama = "ollama"
    local_model = "local_model"


class AgentProviderStatus(str, Enum):
    """
    Configuration state for user-facing agent providers.
    """

    available = "available"
    not_configured = "not_configured"
    coming_soon = "coming_soon"
    local_required = "local_required"


class AgentProviderStatusResponse(BaseModel):
    """
    Public provider configuration metadata for launcher UI.
    """

    id: str
    label: str
    status: AgentProviderStatus
    description: str
    required_config: list[str] = Field(default_factory=list)


class AgentProviderConnectionStatus(str, Enum):
    """
    Local connection state for user-facing agent providers.
    """

    connected = "connected"
    connecting = "connecting"
    disconnected = "disconnected"
    not_configured = "not_configured"
    coming_soon = "coming_soon"


class AgentProviderReadiness(str, Enum):
    """
    Technical readiness for a provider, independent from user connection state.
    """

    ready = "ready"
    auth_required = "auth_required"
    unavailable = "unavailable"
    not_configured = "not_configured"


class AgentProviderPrimaryStatus(str, Enum):
    """
    Single user-facing provider card status.
    """

    connected = "connected"
    connecting = "connecting"
    active = "active"
    disconnected = "disconnected"
    ready = "ready"
    unavailable = "unavailable"
    not_configured = "not_configured"


class AgentProviderMetadataResponse(BaseModel):
    """
    Public provider metadata and local connection state for launcher UI.
    """

    id: str
    label: str
    auth_mode: str
    status: AgentProviderConnectionStatus
    connection: AgentProviderConnectionStatus
    readiness: AgentProviderReadiness
    primary_status: AgentProviderPrimaryStatus
    description: str
    supports_chatgpt_login: bool = False
    supports_api_key: bool = False


class AgentProviderConnectionRequest(BaseModel):
    """
    Request to simulate connecting or disconnecting an agent provider locally.
    """

    provider_id: str
    auth_mode: str | None = None


class AgentProviderApiKeyRequest(BaseModel):
    """
    Request to connect a provider using a server-side API key credential.
    """

    provider_id: str
    api_key: str = Field(..., min_length=1)


class AgentProviderAuthFlowResponse(BaseModel):
    """
    Setup guidance returned before a provider can be marked connected.
    """

    provider_id: str
    status: str = "auth_required"
    message: str
    command: str | None = None
    fallback_command: str | None = None
    auth_url: str | None = None
    verification_url: str | None = None
    device_code: str | None = None
    user_code: str | None = None
    instructions: list[str] = Field(default_factory=list)
    metadata: AgentProviderMetadataResponse


class AgentProviderVerificationResponse(BaseModel):
    """
    Result of verifying a provider connection.
    """

    provider_id: str
    connected: bool
    message: str
    metadata: AgentProviderMetadataResponse


class ChangeScope(str, Enum):
    """
    User-selected task scope guidance for the agent runner.
    """

    only_selected_file = "only_selected_file"
    allow_related_files = "allow_related_files"


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
        default=AgentProvider.codex,
        description="AI agent provider to use for this task.",
    )
    target_file: str | None = Field(
        default=None,
        description="Optional selected repository file path for task context.",
    )
    change_scope: ChangeScope = Field(
        default=ChangeScope.only_selected_file,
        description="Guidance for whether the agent should stay in the target file.",
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
    change_scope: ChangeScope = ChangeScope.only_selected_file
    instruction: str
    agent: AgentProvider
    status: TaskStatus
    changed_files: list[ChangedFile] = Field(default_factory=list)
    commit_sha: str | None = None
    pushed: bool = False
    pushed_at: datetime | None = None
    remote_branch: str | None = None
    last_push_result: str | None = None
    branch_url: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    pr_state: str | None = None
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


class GitHubAuthStartResponse(BaseModel):
    """
    OAuth login bootstrap data.
    """

    auth_url: str
    session_id: str
    state: str


class GitHubAccountResponse(BaseModel):
    """
    Authenticated GitHub account metadata exposed to clients.
    """

    github_user_id: int
    login: str
    name: str | None = None
    avatar_url: str | None = None
    html_url: str | None = None
    scopes: str | None = None
    connected_at: datetime


class GitHubInstallationResponse(BaseModel):
    """
    GitHub App installation metadata exposed to clients.
    """

    installation_id: int
    account_login: str
    account_type: str
    selected_repositories: list[str] = Field(default_factory=list)
    permissions: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class GitHubRepositorySummary(BaseModel):
    """
    Repository metadata returned from the authenticated GitHub repo list.
    """

    id: int
    name: str
    full_name: str
    html_url: str
    clone_url: str
    default_branch: str
    private: bool
    visibility: str
    can_read: bool
    can_push: bool


class GitHubRepositoriesResponse(BaseModel):
    repositories: list[GitHubRepositorySummary] = Field(default_factory=list)


class GitHubInstallationsResponse(BaseModel):
    installations: list[GitHubInstallationResponse] = Field(default_factory=list)


class LogoutResponse(BaseModel):
    status: str
    message: str


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
    branch_url: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    pr_state: str | None = None


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


class TaskPullRequestResponse(BaseModel):
    """
    Response returned after creating a pull request for a pushed task.
    """

    task_id: str
    status: TaskStatus
    message: str
    pr_url: str
    pr_number: int
    pr_state: str


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
