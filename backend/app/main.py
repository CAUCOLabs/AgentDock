from datetime import datetime, timezone
import logging
import re

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from app.agent_providers import (
    AgentProviderError,
    connect_provider_api_key,
    connect_provider,
    disconnect_provider,
    provider_metadata,
    provider_statuses,
    verify_provider_connection,
)
from app.config import Settings, get_settings
from app.diff_service import DiffService, DiffServiceError
from app.github_client import GitHubClient, GitHubClientError
from app.github_auth import (
    GITHUB_APP_NOT_CONFIGURED,
    GitHubAuthError,
    GitHubAuthService,
    GitHubAuthStore,
)
from app.models import (
    AgentProviderStatusResponse,
    AgentProviderApiKeyRequest,
    AgentProviderAuthFlowResponse,
    AgentProviderConnectionRequest,
    AgentProviderMetadataResponse,
    AgentProviderVerificationResponse,
    GitHubAccountResponse,
    GitHubAuthStartResponse,
    GitHubInstallationResponse,
    GitHubInstallationsResponse,
    GitHubRepositoriesResponse,
    LogoutResponse,
    PromptAssistantRefineRequest,
    PromptAssistantRefineResponse,
    RepositoryFilePreviewRequest,
    RepositoryFilePreviewResponse,
    RepositoryFilesRequest,
    RepositoryFilesResponse,
    TaskApprovalRequest,
    TaskApprovalResponse,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskDeleteResponse,
    TaskDiffResponse,
    TaskLogsResponse,
    TaskMergePullRequestResponse,
    TaskPullRequestResponse,
    TaskStatus,
    TaskSummary,
    WorkspaceStatusResponse,
    create_task_id,
    create_working_branch,
)
from app.prompt_assistant import refine_prompt
from app.repo_browser import RepoBrowserError, RepoBrowserService
from app.task_runner import TaskRunner, TaskRunnerError
from app.task_store import TaskStore
from app.workspace import WorkspaceError, WorkspaceManager

logger = logging.getLogger(__name__)

app = FastAPI(
    title="AgentDock API",
    description="Mobile-first command center for AI coding agents.",
    version="0.1.0",
)

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s]+"),
    re.compile(r"(?i)((?:id_token|access_token|refresh_token)=)[^&\s]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s,;]+"),
]


def _redact_error_message(message: str) -> str:
    redacted = message
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(r"\1[REDACTED]" if pattern.groups else "[REDACTED]", redacted)
    return redacted


def _task_creation_error_response(
    *,
    detail: str,
    error_type: str,
    provider: str,
    task_id: str | None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": _redact_error_message(detail),
            "error_type": error_type,
            "provider": provider,
            "task_id": task_id,
        },
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict[str, str | bool]:
    """
    Basic health endpoint.
    """

    return {
        "status": "ok",
        "app": settings.app_name,
        "env": settings.app_env,
        "debug": settings.app_debug,
    }


@app.get(
    "/agent-providers/status",
    response_model=list[AgentProviderStatusResponse],
)
def agent_provider_status(
    settings: Settings = Depends(get_settings),
) -> list[AgentProviderStatusResponse]:
    """
    Return provider configuration metadata for launcher UI.
    """

    return provider_statuses(settings)


@app.get("/agent-providers", response_model=list[AgentProviderMetadataResponse])
def list_agent_providers(
    settings: Settings = Depends(get_settings),
) -> list[AgentProviderMetadataResponse]:
    """
    Return agent provider metadata and simulated local connection state.
    """

    return provider_metadata(settings)


@app.post(
    "/agent-providers/connect",
    response_model=AgentProviderMetadataResponse | AgentProviderAuthFlowResponse,
)
def connect_agent_provider(
    payload: AgentProviderConnectionRequest,
    settings: Settings = Depends(get_settings),
) -> AgentProviderMetadataResponse | AgentProviderAuthFlowResponse:
    """
    Start provider connection.

    Codex returns setup instructions first and is only marked connected after
    verification succeeds.
    """

    try:
        return connect_provider(
            settings,
            provider_id=payload.provider_id,
            auth_mode=payload.auth_mode,
        )
    except AgentProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_redact_error_message(str(exc)),
        ) from exc


@app.post(
    "/agent-providers/verify",
    response_model=AgentProviderVerificationResponse,
)
def verify_agent_provider(
    payload: AgentProviderConnectionRequest,
    settings: Settings = Depends(get_settings),
) -> AgentProviderVerificationResponse:
    """
    Verify a provider connection before persisting activation state.
    """

    try:
        result = verify_provider_connection(
            settings,
            provider_id=payload.provider_id,
            auth_mode=payload.auth_mode,
        )
    except AgentProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_redact_error_message(str(exc)),
        ) from exc

    if not result.connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_redact_error_message(result.message),
        )

    return result


@app.post("/agent-providers/api-key", response_model=AgentProviderMetadataResponse)
def connect_agent_provider_api_key(
    payload: AgentProviderApiKeyRequest,
    settings: Settings = Depends(get_settings),
) -> AgentProviderMetadataResponse:
    """
    Accept a provider API key and persist provider auth server-side.
    """

    try:
        return connect_provider_api_key(
            settings,
            provider_id=payload.provider_id,
            api_key=payload.api_key,
        )
    except AgentProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_redact_error_message(str(exc)),
        ) from exc


@app.post("/agent-providers/disconnect", response_model=AgentProviderMetadataResponse)
def disconnect_agent_provider(
    payload: AgentProviderConnectionRequest,
    settings: Settings = Depends(get_settings),
) -> AgentProviderMetadataResponse:
    """
    Disconnect an agent provider from local simulated connection state.
    """

    try:
        return disconnect_provider(settings, provider_id=payload.provider_id)
    except AgentProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_redact_error_message(str(exc)),
        ) from exc


def get_task_store(settings: Settings = Depends(get_settings)) -> TaskStore:
    """
    Return the SQLite-backed task store.
    """

    return TaskStore(settings)


def get_github_auth_service(
    settings: Settings = Depends(get_settings),
) -> GitHubAuthService:
    return GitHubAuthService(settings)


async def get_optional_github_token(
    x_agentdock_session: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> str | None:
    try:
        return await GitHubAuthService(settings).token_for_session(x_agentdock_session)
    except GitHubAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc


@app.on_event("startup")
def initialize_task_store() -> None:
    """
    Initialize SQLite storage and run conservative task recovery at startup.
    """

    TaskStore(get_settings())
    GitHubAuthStore(get_settings())


@app.get("/")
def root() -> dict[str, str]:
    """
    Root endpoint.
    """

    return {
        "message": "AgentDock API is running",
        "docs": "/docs",
        "health": "/health",
    }


def github_app_http_error(exc: GitHubAuthError) -> HTTPException:
    return HTTPException(
        status_code=(
            status.HTTP_400_BAD_REQUEST
            if str(exc) == GITHUB_APP_NOT_CONFIGURED
            else status.HTTP_401_UNAUTHORIZED
        ),
        detail=str(exc),
    )


@app.get("/auth/github-app/start", response_model=GitHubAuthStartResponse)
@app.post("/auth/github-app/start", response_model=GitHubAuthStartResponse)
def start_github_app_auth(
    auth_service: GitHubAuthService = Depends(get_github_auth_service),
) -> GitHubAuthStartResponse:
    try:
        auth_url, session_id, state = auth_service.start_app_installation()
    except GitHubAuthError as exc:
        raise github_app_http_error(exc) from exc

    return GitHubAuthStartResponse(
        auth_url=auth_url,
        session_id=session_id,
        state=state,
    )


@app.get("/auth/github-app/callback", response_class=HTMLResponse)
async def complete_github_app_auth(
    installation_id: int,
    state: str,
    auth_service: GitHubAuthService = Depends(get_github_auth_service),
) -> str:
    try:
        installation = await auth_service.complete_app_callback(
            installation_id=installation_id,
            state=state,
        )
    except GitHubAuthError as exc:
        raise github_app_http_error(exc) from exc

    return (
        "<!doctype html><html><body>"
        "<h1>GitHub App installed</h1>"
        f"<p>Connected {installation.account_login}. "
        "You can return to AgentDock.</p>"
        "</body></html>"
    )


@app.get("/auth/github-app/me", response_model=GitHubInstallationResponse)
def get_github_app_me(
    x_agentdock_session: str | None = Header(default=None),
    auth_service: GitHubAuthService = Depends(get_github_auth_service),
) -> GitHubInstallationResponse:
    try:
        return auth_service.get_app_session(x_agentdock_session).installation
    except GitHubAuthError as exc:
        raise github_app_http_error(exc) from exc


@app.get(
    "/auth/github-app/installations",
    response_model=GitHubInstallationsResponse,
)
def list_github_app_installations(
    x_agentdock_session: str | None = Header(default=None),
    auth_service: GitHubAuthService = Depends(get_github_auth_service),
) -> GitHubInstallationsResponse:
    if x_agentdock_session:
        try:
            return GitHubInstallationsResponse(
                installations=[
                    auth_service.get_app_session(x_agentdock_session).installation
                ]
            )
        except GitHubAuthError as exc:
            raise github_app_http_error(exc) from exc

    return GitHubInstallationsResponse(
        installations=auth_service.list_app_installations()
    )


@app.get("/auth/github-app/repos", response_model=GitHubRepositoriesResponse)
async def list_github_app_repositories(
    x_agentdock_session: str | None = Header(default=None),
    auth_service: GitHubAuthService = Depends(get_github_auth_service),
) -> GitHubRepositoriesResponse:
    try:
        repositories = await auth_service.list_app_repositories(x_agentdock_session)
    except GitHubAuthError as exc:
        raise github_app_http_error(exc) from exc

    return GitHubRepositoriesResponse(repositories=repositories)


@app.post("/auth/github/start", response_model=GitHubAuthStartResponse)
def start_github_auth(
    settings: Settings = Depends(get_settings),
    auth_service: GitHubAuthService = Depends(get_github_auth_service),
) -> GitHubAuthStartResponse:
    if not settings.is_development:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OAuth fallback is only available in development.",
        )

    try:
        auth_url, session_id, state = auth_service.start_login()
    except GitHubAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return GitHubAuthStartResponse(
        auth_url=auth_url,
        session_id=session_id,
        state=state,
    )


@app.get("/auth/github/callback", response_class=HTMLResponse)
async def complete_github_auth(
    code: str,
    state: str,
    settings: Settings = Depends(get_settings),
    auth_service: GitHubAuthService = Depends(get_github_auth_service),
) -> str:
    if not settings.is_development:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OAuth fallback is only available in development.",
        )

    try:
        account = await auth_service.complete_callback(code=code, state=state)
    except GitHubAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return (
        "<!doctype html><html><body>"
        "<h1>GitHub connected</h1>"
        f"<p>Signed in as {account.login}. You can return to AgentDock.</p>"
        "</body></html>"
    )


@app.get("/auth/github/me", response_model=GitHubAccountResponse)
def get_github_me(
    x_agentdock_session: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
    auth_service: GitHubAuthService = Depends(get_github_auth_service),
) -> GitHubAccountResponse:
    if not settings.is_development:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OAuth fallback is only available in development.",
        )

    try:
        return auth_service.get_session(x_agentdock_session).account
    except GitHubAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc


@app.get("/auth/github/repos", response_model=GitHubRepositoriesResponse)
async def list_github_repositories(
    x_agentdock_session: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
    auth_service: GitHubAuthService = Depends(get_github_auth_service),
) -> GitHubRepositoriesResponse:
    if not settings.is_development:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OAuth fallback is only available in development.",
        )

    try:
        repositories = await auth_service.list_repositories(x_agentdock_session)
    except GitHubAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    return GitHubRepositoriesResponse(repositories=repositories)


@app.post("/auth/logout", response_model=LogoutResponse)
@app.post("/auth/github-app/logout", response_model=LogoutResponse)
def logout(
    x_agentdock_session: str | None = Header(default=None),
    auth_service: GitHubAuthService = Depends(get_github_auth_service),
) -> LogoutResponse:
    auth_service.logout(x_agentdock_session)
    return LogoutResponse(status="logged_out", message="Local session revoked.")


@app.post(
    "/repositories/files",
    response_model=RepositoryFilesResponse,
)
def list_repository_files(
    payload: RepositoryFilesRequest,
    settings: Settings = Depends(get_settings),
    github_token: str | None = Depends(get_optional_github_token),
) -> RepositoryFilesResponse:
    """
    Return a read-only repository file tree for target file selection.
    """

    try:
        files = RepoBrowserService(settings, github_token=github_token).list_files(
            repo_url=payload.repo_url,
            base_branch=payload.base_branch,
        )
    except RepoBrowserError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return RepositoryFilesResponse(
        repo_url=payload.repo_url,
        base_branch=payload.base_branch,
        files=files,
    )


@app.post(
    "/repositories/file-preview",
    response_model=RepositoryFilePreviewResponse,
)
def preview_repository_file(
    payload: RepositoryFilePreviewRequest,
    settings: Settings = Depends(get_settings),
    github_token: str | None = Depends(get_optional_github_token),
) -> RepositoryFilePreviewResponse:
    """
    Return a bounded, read-only text preview for one repository file.
    """

    try:
        content, size, truncated = RepoBrowserService(
            settings,
            github_token=github_token,
        ).preview_file(
            repo_url=payload.repo_url,
            base_branch=payload.base_branch,
            file_path=payload.path,
        )
    except RepoBrowserError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return RepositoryFilePreviewResponse(
        repo_url=payload.repo_url,
        base_branch=payload.base_branch,
        path=payload.path,
        size=size,
        truncated=truncated,
        content=content,
    )


@app.post(
    "/prompt-assistant/refine",
    response_model=PromptAssistantRefineResponse,
)
def refine_prompt_assistant(
    payload: PromptAssistantRefineRequest,
    settings: Settings = Depends(get_settings),
) -> PromptAssistantRefineResponse:
    """
    Refine a rough idea into a Codex-ready prompt without launching Codex.
    """

    return refine_prompt(payload, settings)


@app.post(
    "/tasks",
    response_model=TaskCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_task(
    payload: TaskCreateRequest,
    settings: Settings = Depends(get_settings),
    task_store: TaskStore = Depends(get_task_store),
    github_token: str | None = Depends(get_optional_github_token),
) -> TaskCreateResponse | JSONResponse:
    """
    Create and run a new agent task.

    Current MVP behavior:
    - creates task metadata
    - clones the target repository
    - creates a safe working branch
    - runs the selected agent
    - stores the generated diff for review
    """

    now = datetime.now(timezone.utc)
    task_id = create_task_id()
    working_branch = create_working_branch(task_id)

    try:
        target_file = payload.target_file.strip() if payload.target_file else None

        if target_file:
            RepoBrowserService.validate_relative_path(target_file)

        runner_instruction = RepoBrowserService(settings).task_instruction(
            payload.instruction,
            target_file,
            payload.change_scope.value,
        )
    except RepoBrowserError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    task = TaskSummary(
        task_id=task_id,
        repo_url=str(payload.repo_url),
        base_branch=payload.base_branch,
        working_branch=working_branch,
        workspace_path=str(settings.workspace_root / task_id / "repo"),
        target_file=target_file,
        change_scope=payload.change_scope,
        instruction=payload.instruction,
        agent=payload.agent,
        status=TaskStatus.running,
        changed_files=[],
        created_at=now,
        updated_at=now,
    )

    try:
        task_store.save_task(task)
        task_store.append_log(task_id, "info", "Task created.")
        if target_file:
            task_store.append_log(
                task_id,
                "info",
                f"Target file selected: {target_file}",
            )
        task_store.append_log(
            task_id,
            "info",
            f"Change scope selected: {payload.change_scope.value}",
        )
        task_store.append_log(task_id, "info", "Task running.")
    except Exception as exc:
        logger.exception(
            "Task creation persistence failed for task_id=%s provider=%s",
            task_id,
            payload.agent.value,
        )
        return _task_creation_error_response(
            detail="Task creation failed before runner startup.",
            error_type=exc.__class__.__name__,
            provider=payload.agent.value,
            task_id=task_id,
        )

    try:
        runner = TaskRunner(settings, github_token=github_token)

        result = runner.run_task(
            task_id=task_id,
            repo_url=str(payload.repo_url),
            base_branch=payload.base_branch,
            working_branch=working_branch,
            instruction=runner_instruction,
            agent=payload.agent,
            target_file=target_file,
            change_scope=payload.change_scope.value,
            log_event=lambda level, message: task_store.append_log(
                task_id,
                level,
                message,
            ),
        )

        task.status = TaskStatus.waiting_for_approval
        task.changed_files = result.changed_files
        task.updated_at = datetime.now(timezone.utc)

        task_store.save_task(task)
        for level, message in result.log_messages:
            task_store.append_log(task_id, level, message)
        task_store.append_log(task_id, "info", "Task completed by runner.")
        task_store.save_diff(task_id, result.diff)
        task_store.append_log(task_id, "info", "Diff ready for approval.")

        return TaskCreateResponse(
            task_id=task_id,
            status=task.status,
            message=result.message,
        )

    except TaskRunnerError as exc:
        logger.exception(
            "Task creation runner failed for task_id=%s provider=%s",
            task_id,
            exc.provider or payload.agent.value,
        )
        task.status = TaskStatus.failed
        task.error = _redact_error_message(str(exc))
        task.updated_at = datetime.now(timezone.utc)

        try:
            task_store.save_task(task)
            task_store.save_diff(task_id, "")
            for level, message in exc.log_messages:
                task_store.append_log(task_id, level, _redact_error_message(message))
            task_store.append_log(
                task_id,
                "error",
                f"Task runner failed: {_redact_error_message(str(exc))}",
            )
        except Exception:
            logger.exception(
                "Failed to persist task failure for task_id=%s provider=%s",
                task_id,
                exc.provider or payload.agent.value,
            )

        return _task_creation_error_response(
            detail=str(exc),
            error_type=exc.error_type,
            provider=exc.provider or payload.agent.value,
            task_id=task_id,
        )
    except Exception as exc:
        logger.exception(
            "Unexpected task creation failure for task_id=%s provider=%s",
            task_id,
            payload.agent.value,
        )
        task.status = TaskStatus.failed
        task.error = _redact_error_message(str(exc))
        task.updated_at = datetime.now(timezone.utc)

        try:
            task_store.save_task(task)
            task_store.save_diff(task_id, "")
            task_store.append_log(
                task_id,
                "error",
                f"Unexpected task creation failure: {_redact_error_message(str(exc))}",
            )
        except Exception:
            logger.exception(
                "Failed to persist unexpected task failure for task_id=%s provider=%s",
                task_id,
                payload.agent.value,
            )

        return _task_creation_error_response(
            detail=str(exc),
            error_type=exc.__class__.__name__,
            provider=payload.agent.value,
            task_id=task_id,
        )


@app.get("/tasks", response_model=list[TaskSummary])
def list_tasks(
    task_store: TaskStore = Depends(get_task_store),
) -> list[TaskSummary]:
    """
    List all stored tasks.
    """

    return task_store.list_tasks()


@app.get("/tasks/{task_id}", response_model=TaskSummary)
def get_task(
    task_id: str,
    task_store: TaskStore = Depends(get_task_store),
) -> TaskSummary:
    """
    Get a task by id.
    """

    task = task_store.get_task(task_id)

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task not found: {task_id}",
        )

    return task


@app.delete("/tasks/{task_id}", response_model=TaskDeleteResponse)
def delete_task(
    task_id: str,
    settings: Settings = Depends(get_settings),
    task_store: TaskStore = Depends(get_task_store),
) -> TaskDeleteResponse:
    """
    Delete a task, its stored diff, and its local workspace.
    """

    task = task_store.get_task(task_id)

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task not found: {task_id}",
        )

    task_store.append_log(task_id, "info", "Task deleted.")
    task_store.delete_task(task_id)

    try:
        WorkspaceManager(settings).delete_workspace(task_id)
    except WorkspaceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "task_id": task_id,
                "error": str(exc),
            },
        ) from exc

    return TaskDeleteResponse(
        task_id=task_id,
        status="deleted",
        message="Task and workspace deleted.",
    )


@app.get("/tasks/{task_id}/logs", response_model=TaskLogsResponse)
def get_task_logs(
    task_id: str,
    task_store: TaskStore = Depends(get_task_store),
) -> TaskLogsResponse:
    """
    Get lifecycle events for a task.
    """

    task = task_store.get_task(task_id)

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task not found: {task_id}",
        )

    return TaskLogsResponse(
        task_id=task_id,
        events=task_store.get_logs(task_id),
    )


@app.get("/tasks/{task_id}/diff", response_model=TaskDiffResponse)
def get_task_diff(
    task_id: str,
    task_store: TaskStore = Depends(get_task_store),
) -> TaskDiffResponse:
    """
    Get the generated diff for a task.
    """

    task = task_store.get_task(task_id)

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task not found: {task_id}",
        )

    return TaskDiffResponse(
        task_id=task_id,
        status=task.status,
        diff=task_store.get_diff(task_id),
        changed_files=task.changed_files,
    )


@app.get(
    "/tasks/{task_id}/workspace-status",
    response_model=WorkspaceStatusResponse,
)
def get_workspace_status(
    task_id: str,
    settings: Settings = Depends(get_settings),
    task_store: TaskStore = Depends(get_task_store),
) -> WorkspaceStatusResponse:
    """
    Return read-only Git workspace status for a task repository.
    """

    task = task_store.get_task(task_id)

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task not found: {task_id}",
        )

    try:
        workspace_manager = WorkspaceManager(settings)
        workspace_path = workspace_manager.get_workspace(task_id)

        if not workspace_path.exists():
            snapshot = task_store.get_workspace_status_snapshot(task_id)

            if snapshot is not None:
                return snapshot

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workspace repository not found for task: {task_id}",
            )

        repo_path = workspace_manager.repo_path(task_id)
    except WorkspaceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "task_id": task_id,
                "error": str(exc),
            },
        ) from exc

    if not repo_path.exists():
        snapshot = task_store.get_workspace_status_snapshot(task_id)

        if snapshot is not None:
            return snapshot

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace repository not found for task: {task_id}",
        )

    try:
        github_client = GitHubClient(settings)
        repo = github_client.open_repository(repo_path)
        remote_info = github_client.get_remote_info(repo, task.base_branch)
        diff_service = DiffService(repo_path)
        changed_files = diff_service.get_changed_files()
    except (GitHubClientError, DiffServiceError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "task_id": task_id,
                "error": str(exc),
            },
        ) from exc

    try:
        branch = repo.active_branch.name
    except TypeError:
        branch = None

    try:
        head_commit = repo.head.commit.hexsha
    except ValueError:
        head_commit = None

    response = WorkspaceStatusResponse(
        task_id=task_id,
        repo_path=str(repo_path),
        branch=branch,
        head_commit=head_commit,
        remote_url=remote_info.remote_url,
        has_valid_origin=remote_info.has_valid_origin,
        default_branch=remote_info.default_branch,
        push_enabled=remote_info.push_enabled,
        pr_creation_enabled=remote_info.pr_creation_enabled,
        is_dirty=repo.is_dirty(untracked_files=True),
        has_untracked_files=bool(repo.untracked_files),
        changed_files=changed_files,
        commit_sha=task.commit_sha,
        pushed=task.pushed,
        pushed_at=task.pushed_at,
        remote_branch=task.remote_branch,
        last_push_result=task.last_push_result,
        branch_url=task.branch_url,
        pr_url=task.pr_url,
        pr_number=task.pr_number,
        pr_state=task.pr_state,
    )
    task_store.save_workspace_snapshot(
        task_id=task_id,
        repo_path=response.repo_path,
        branch=response.branch,
        head_commit=response.head_commit,
        is_dirty=response.is_dirty,
        has_untracked_files=response.has_untracked_files,
        changed_files=response.changed_files,
        remote_url=response.remote_url,
        has_valid_origin=response.has_valid_origin,
        default_branch=response.default_branch,
        push_enabled=response.push_enabled,
        pr_creation_enabled=response.pr_creation_enabled,
        commit_sha=response.commit_sha,
        pushed=response.pushed,
        pushed_at=response.pushed_at,
        remote_branch=response.remote_branch,
        last_push_result=response.last_push_result,
        pr_url=response.pr_url,
        pr_number=response.pr_number,
        pr_state=response.pr_state,
    )

    return response


@app.post("/tasks/{task_id}/approval", response_model=TaskApprovalResponse)
async def approve_or_reject_task(
    task_id: str,
    payload: TaskApprovalRequest,
    settings: Settings = Depends(get_settings),
    task_store: TaskStore = Depends(get_task_store),
) -> TaskApprovalResponse:
    """
    Approve or reject a task.

    Approval creates a local commit in the task workspace repository.
    Push and PR creation are optional and only run when explicitly enabled.
    """

    task = task_store.get_task(task_id)

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task not found: {task_id}",
        )

    if task.status != TaskStatus.waiting_for_approval:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Task {task_id} is not waiting for approval. "
                f"Current status: {task.status}."
            ),
        )

    now = datetime.now(timezone.utc)

    if not payload.approve:
        task_store.append_log(task_id, "info", "Task rejected.")

        try:
            workspace_manager = WorkspaceManager(settings)
            repo_path = workspace_manager.repo_path(task_id)
            github_client = GitHubClient(settings)
            repo = github_client.open_repository(repo_path)
            remote_info = github_client.get_remote_info(repo, task.base_branch)
            github_client.revert_and_clean(repo)
            diff_service = DiffService(repo_path)
            task_store.save_workspace_snapshot(
                task_id=task_id,
                repo_path=str(repo_path),
                branch=repo.active_branch.name,
                head_commit=repo.head.commit.hexsha,
                is_dirty=repo.is_dirty(untracked_files=True),
                has_untracked_files=bool(repo.untracked_files),
                changed_files=diff_service.get_changed_files(),
                remote_url=remote_info.remote_url,
                has_valid_origin=remote_info.has_valid_origin,
                default_branch=remote_info.default_branch,
                push_enabled=remote_info.push_enabled,
                pr_creation_enabled=remote_info.pr_creation_enabled,
                commit_sha=task.commit_sha,
                pushed=task.pushed,
                pushed_at=task.pushed_at,
                remote_branch=task.remote_branch,
                last_push_result=task.last_push_result,
                pr_url=task.pr_url,
                pr_number=task.pr_number,
                pr_state=task.pr_state,
            )
        except (DiffServiceError, GitHubClientError, WorkspaceError) as exc:
            task.status = TaskStatus.failed
            task.error = str(exc)
            task.updated_at = now
            task_store.save_task(task)
            task_store.append_log(task_id, "error", f"Workspace cleanup failed: {exc}")

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "task_id": task_id,
                    "error": str(exc),
                },
            ) from exc

        task.status = TaskStatus.rejected
        task.updated_at = now
        task.rejected_at = now
        task_store.save_task(task)
        task_store.append_log(task_id, "info", "Workspace reverted.")
        task_store.append_log(task_id, "info", "Workspace cleaned.")

        return TaskApprovalResponse(
            task_id=task_id,
            status=task.status,
            message=payload.message or "Task rejected.",
        )

    try:
        task_store.append_log(task_id, "info", "Commit started.")
        workspace_manager = WorkspaceManager(settings)
        repo_path = workspace_manager.repo_path(task_id)
        github_client = GitHubClient(settings)
        repo = github_client.open_repository(repo_path)
        remote_info = github_client.get_remote_info(repo, task.base_branch)
        commit_sha = github_client.commit_all(repo, f"AgentDock task: {task_id}")
    except (GitHubClientError, WorkspaceError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "task_id": task_id,
                "error": str(exc),
            },
        ) from exc

    task.status = TaskStatus.committed
    task.commit_sha = commit_sha
    task.pushed = False
    task.pushed_at = None
    task.remote_branch = None
    task.last_push_result = "Push not attempted."
    task.pr_url = None
    task.pr_number = None
    task.pr_state = None
    task.error = None
    task.updated_at = now
    task.completed_at = now
    task_store.append_log(task_id, "info", f"Task locally committed: {commit_sha}.")
    task_store.append_log(task_id, "info", "Commit completed.")

    try:
        diff_service = DiffService(repo_path)
        task_store.save_workspace_snapshot(
            task_id=task_id,
            repo_path=str(repo_path),
            branch=repo.active_branch.name,
            head_commit=repo.head.commit.hexsha,
            is_dirty=repo.is_dirty(untracked_files=True),
            has_untracked_files=bool(repo.untracked_files),
            changed_files=diff_service.get_changed_files(),
            remote_url=remote_info.remote_url,
            has_valid_origin=remote_info.has_valid_origin,
            default_branch=remote_info.default_branch,
            push_enabled=remote_info.push_enabled,
            pr_creation_enabled=remote_info.pr_creation_enabled,
            commit_sha=task.commit_sha,
            pushed=False,
            pushed_at=None,
            remote_branch=None,
            last_push_result=task.last_push_result,
            pr_url=None,
            pr_number=None,
            pr_state=None,
        )
    except DiffServiceError as exc:
        task_store.append_log(
            task_id,
            "warning",
            f"Workspace snapshot failed after commit: {exc}",
        )

    task_store.save_task(task)
    if not settings.allow_push:
        task_store.append_log(task_id, "info", "Push unavailable because ALLOW_PUSH=false.")

    return TaskApprovalResponse(
        task_id=task_id,
        status=task.status,
        message=f"Task committed locally: {commit_sha}.",
    )


@app.post("/tasks/{task_id}/push", response_model=TaskApprovalResponse)
async def push_committed_task(
    task_id: str,
    settings: Settings = Depends(get_settings),
    task_store: TaskStore = Depends(get_task_store),
) -> TaskApprovalResponse:
    """
    Push an already committed task branch to origin.
    """

    task = task_store.get_task(task_id)

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task not found: {task_id}",
        )

    if not settings.allow_push:
        message = "Push is disabled. Set ALLOW_PUSH=true to enable it."
        task.last_push_result = message
        task.updated_at = datetime.now(timezone.utc)
        task_store.save_task(task)
        task_store.append_log(task_id, "warning", message)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "task_id": task_id,
                "error": message,
            },
        )

    if task.status != TaskStatus.committed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Task {task_id} is not committed and ready to push. "
                f"Current status: {task.status}."
            ),
        )

    if not task.commit_sha:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task {task_id} has no commit to push.",
        )

    if not task.working_branch:
        task.error = "Push is enabled, but the task working branch is missing."
        task_store.save_task(task)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "task_id": task_id,
                "error": task.error,
            },
        )

    try:
        task_store.append_log(task_id, "info", "Push started.")
        workspace_manager = WorkspaceManager(settings)
        repo_path = workspace_manager.repo_path(task_id)
        github_client = GitHubClient(settings)
        repo = github_client.open_repository(repo_path)
        remote_info = github_client.get_remote_info(repo, task.base_branch)
        push_result = github_client.push_branch(
            repo,
            task.working_branch,
            default_branch=task.base_branch,
        )

        for level, message in push_result.log_messages():
            task_store.append_log(task_id, level, message)

        task.last_push_result = push_result.message
        task.remote_branch = push_result.remote_branch

        if not push_result.success:
            task.pushed = False
            task.error = push_result.message
            task.status = TaskStatus.committed
            task.updated_at = datetime.now(timezone.utc)
            task_store.save_task(task)
            task_store.append_log(
                task_id,
                "error",
                f"Push failed: {push_result.message}",
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "task_id": task_id,
                    "error": push_result.message,
                },
            )

        now = datetime.now(timezone.utc)
        task.status = TaskStatus.pushed
        task.pushed = True
        task.pushed_at = now
        task.error = None
        task.updated_at = now
        task_store.append_log(
            task_id,
            "info",
            f"Push completed for branch {task.working_branch}.",
        )
    except GitHubClientError as exc:
        task.pushed = False
        task.status = TaskStatus.committed
        task.error = str(exc)
        task.last_push_result = str(exc)
        task.updated_at = datetime.now(timezone.utc)
        task_store.save_task(task)
        task_store.append_log(task_id, "error", f"Push failed: {exc}")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "task_id": task_id,
                "error": str(exc),
            },
        ) from exc

    try:
        diff_service = DiffService(repo_path)
        task_store.save_workspace_snapshot(
            task_id=task_id,
            repo_path=str(repo_path),
            branch=repo.active_branch.name,
            head_commit=repo.head.commit.hexsha,
            is_dirty=repo.is_dirty(untracked_files=True),
            has_untracked_files=bool(repo.untracked_files),
            changed_files=diff_service.get_changed_files(),
            remote_url=remote_info.remote_url,
            has_valid_origin=remote_info.has_valid_origin,
            default_branch=remote_info.default_branch,
            push_enabled=remote_info.push_enabled,
            pr_creation_enabled=remote_info.pr_creation_enabled,
            commit_sha=task.commit_sha,
            pushed=task.pushed,
            pushed_at=task.pushed_at,
            remote_branch=task.remote_branch,
            last_push_result=task.last_push_result,
            pr_url=task.pr_url,
            pr_number=task.pr_number,
            pr_state=task.pr_state,
        )
    except DiffServiceError as exc:
        task_store.append_log(
            task_id,
            "warning",
            f"Workspace snapshot failed after push: {exc}",
        )

    task_store.save_task(task)

    return TaskApprovalResponse(
        task_id=task_id,
        status=task.status,
        message=(
            "Task pushed branch "
            f"{task.working_branch}: {task.commit_sha}."
        ),
    )


@app.post("/tasks/{task_id}/pull-request", response_model=TaskPullRequestResponse)
async def create_task_pull_request(
    task_id: str,
    settings: Settings = Depends(get_settings),
    task_store: TaskStore = Depends(get_task_store),
) -> TaskPullRequestResponse:
    """
    Create a pull request from a pushed AgentDock task branch.
    """

    task = task_store.get_task(task_id)

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task not found: {task_id}",
        )

    if task.status != TaskStatus.pushed or not task.pushed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Task {task_id} is not pushed and ready for PR creation. "
                f"Current status: {task.status}."
            ),
        )

    if not settings.allow_pr_creation:
        message = "PR creation is disabled. Set ALLOW_PR_CREATION=true to enable it."
        task.error = message
        task.updated_at = datetime.now(timezone.utc)
        task_store.save_task(task)
        task_store.append_log(task_id, "warning", message)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "task_id": task_id,
                "error": message,
            },
        )

    if not task.working_branch:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task {task_id} has no pushed branch.",
        )

    if not task.commit_sha:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task {task_id} has no commit SHA for PR metadata.",
        )

    try:
        workspace_manager = WorkspaceManager(settings)
        repo_path = workspace_manager.repo_path(task_id)
        workspace_client = GitHubClient(settings)
        repo = workspace_client.open_repository(repo_path)
        github_token = workspace_client.token_from_origin_remote(repo)
    except (GitHubClientError, WorkspaceError):
        github_token = None

    github_client = GitHubClient(settings, github_token=github_token)
    body = (
        "Automated AgentDock task.\n\n"
        f"Task prompt:\n{task.instruction}\n\n"
        f"Target file:\n{task.target_file or 'None'}\n\n"
        f"Agent provider:\n{task.agent.value}\n\n"
        f"Commit SHA:\n{task.commit_sha}"
    )

    try:
        task_store.append_log(task_id, "info", "PR creation started.")
        pr_result = await github_client.create_pull_request(
            repo_url=task.repo_url,
            head_branch=task.working_branch,
            base_branch=task.base_branch,
            title=f"AgentDock task: {task_id}",
            body=body,
        )
    except GitHubClientError as exc:
        task.status = TaskStatus.pushed
        task.error = str(exc)
        task.updated_at = datetime.now(timezone.utc)
        task_store.save_task(task)
        task_store.append_log(task_id, "error", f"PR creation failed: {exc}")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "task_id": task_id,
                "error": str(exc),
            },
        ) from exc

    task.status = TaskStatus.pull_request_opened
    task.pr_url = pr_result.url
    task.pr_number = pr_result.number
    task.pr_state = pr_result.state
    task.error = None
    task.updated_at = datetime.now(timezone.utc)
    task_store.save_task(task)
    task_store.append_log(task_id, "info", f"PR created: {pr_result.url}")
    task_store.append_log(task_id, "info", "PR creation completed.")

    return TaskPullRequestResponse(
        task_id=task_id,
        status=task.status,
        message=f"Pull request #{pr_result.number} opened: {pr_result.url}",
        pr_url=pr_result.url,
        pr_number=pr_result.number,
        pr_state=pr_result.state,
    )


@app.post(
    "/tasks/{task_id}/merge-pull-request",
    response_model=TaskMergePullRequestResponse,
)
async def merge_task_pull_request(
    task_id: str,
    settings: Settings = Depends(get_settings),
    task_store: TaskStore = Depends(get_task_store),
) -> TaskMergePullRequestResponse:
    """
    Merge an opened AgentDock pull request.
    """

    task = task_store.get_task(task_id)

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task not found: {task_id}",
        )

    if (
        task.status != TaskStatus.pull_request_opened
        or task.pr_number is None
        or task.pr_state != "open"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Task {task_id} does not have an open pull request to merge. "
                f"Current status: {task.status}; PR state: {task.pr_state or 'none'}."
            ),
        )

    github_token, token_source = _task_github_token(task_id, settings)
    github_client = GitHubClient(settings, github_token=github_token)
    merge_method = settings.github_pr_merge_method.lower()
    scope = task.target_file or task.change_scope.value
    commit_message = (
        f"Task prompt:\n{task.instruction}\n\n"
        f"Target file or change scope:\n{scope}\n\n"
        f"Agent provider:\n{task.agent.value}\n\n"
        f"Commit SHA:\n{task.commit_sha or 'None'}\n\n"
        f"PR number:\n#{task.pr_number}"
    )

    try:
        task_store.append_log(task_id, "info", "Merge started.")
        merge_result = await github_client.merge_pull_request(
            repo_url=task.repo_url,
            pr_number=task.pr_number,
            commit_title=f"AgentDock task: {task_id}",
            commit_message=commit_message,
            merge_method=merge_method,
        )
    except GitHubClientError as exc:
        task.status = TaskStatus.pull_request_opened
        task.pr_state = "open"
        task.error = str(exc)
        task.updated_at = datetime.now(timezone.utc)
        task_store.save_task(task)
        task_store.append_log(task_id, "error", f"PR merge failed: {exc}")
        raise HTTPException(
            status_code=_merge_error_status(str(exc)),
            detail={
                "task_id": task_id,
                "error": str(exc),
            },
        ) from exc

    if not merge_result.merged or not merge_result.sha:
        message = merge_result.message or "Pull request was not merged."
        task.status = TaskStatus.pull_request_opened
        task.pr_state = "open"
        task.error = message
        task.updated_at = datetime.now(timezone.utc)
        task_store.save_task(task)
        task_store.append_log(task_id, "error", f"PR merge failed: {message}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "task_id": task_id,
                "error": message,
            },
        )

    try:
        pr_status = await github_client.get_pull_request_status(
            task.repo_url,
            task.pr_number,
        )
    except GitHubClientError as exc:
        task.status = TaskStatus.pull_request_opened
        task.pr_state = "open"
        task.error = str(exc)
        task.updated_at = datetime.now(timezone.utc)
        task_store.save_task(task)
        task_store.append_log(
            task_id,
            "error",
            f"PR merge verification failed: {exc}",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "task_id": task_id,
                "error": str(exc),
            },
        ) from exc

    if pr_status.state == "open" or not pr_status.merged:
        message = "Pull request is still open. Merge was not completed."
        task.status = TaskStatus.pull_request_opened
        task.pr_state = pr_status.state or "open"
        task.error = message
        task.updated_at = datetime.now(timezone.utc)
        task_store.save_task(task)
        task_store.append_log(task_id, "error", f"PR merge incomplete: {message}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "task_id": task_id,
                "error": message,
            },
        )

    merged_at = datetime.now(timezone.utc)
    task.status = TaskStatus.merged
    task.pr_state = "merged"
    task.merged_at = merged_at
    task.merge_commit_sha = pr_status.merge_commit_sha or merge_result.sha
    task.merged_by = pr_status.merged_by or "AgentDock"
    task.merged_source = token_source
    task.error = None
    task.updated_at = merged_at
    task_store.save_task(task)
    task_store.append_log(
        task_id,
        "info",
        f"PR #{task.pr_number} merged: {task.merge_commit_sha}",
    )
    task_store.append_log(task_id, "info", "Merge completed.")

    if settings.delete_branch_after_merge and task.working_branch:
        try:
            await github_client.delete_remote_branch(task.repo_url, task.working_branch)
            task_store.append_log(
                task_id,
                "info",
                f"Deleted remote branch {task.working_branch}.",
            )
        except GitHubClientError as exc:
            task_store.append_log(
                task_id,
                "warning",
                f"Remote branch cleanup failed: {exc}",
            )

    return TaskMergePullRequestResponse(
        task_id=task_id,
        status=task.status,
        message=f"Pull request #{task.pr_number} merged.",
        pr_url=task.pr_url or "",
        pr_number=task.pr_number,
        pr_state=task.pr_state or "merged",
        merged_at=merged_at,
        merge_commit_sha=task.merge_commit_sha,
        merged_by=task.merged_by,
        merged_source=task.merged_source,
    )


def _task_github_token(task_id: str, settings: Settings) -> tuple[str | None, str | None]:
    try:
        workspace_manager = WorkspaceManager(settings)
        repo_path = workspace_manager.repo_path(task_id)
        workspace_client = GitHubClient(settings)
        repo = workspace_client.open_repository(repo_path)
        github_token = workspace_client.token_from_origin_remote(repo)
        if github_token:
            source = "github_app" if github_token.startswith("ghs_") else "remote"
            return github_token, source
    except (GitHubClientError, WorkspaceError):
        pass

    return settings.github_token, "github_token" if settings.github_token else None


def _merge_error_status(message: str) -> int:
    lowered = message.lower()

    if "permission" in lowered or "github app needs" in lowered:
        return status.HTTP_403_FORBIDDEN

    if "conflict" in lowered or "not mergeable" in lowered:
        return status.HTTP_409_CONFLICT

    if "already merged" in lowered or "closed" in lowered:
        return status.HTTP_409_CONFLICT

    if "review required" in lowered or "branch protection" in lowered:
        return status.HTTP_409_CONFLICT

    return status.HTTP_500_INTERNAL_SERVER_ERROR
