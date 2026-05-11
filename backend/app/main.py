from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.diff_service import DiffService, DiffServiceError
from app.github_client import GitHubClient, GitHubClientError
from app.models import (
    TaskApprovalRequest,
    TaskApprovalResponse,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskDeleteResponse,
    TaskDiffResponse,
    TaskStatus,
    TaskSummary,
    WorkspaceStatusResponse,
    create_task_id,
    create_working_branch,
)
from app.task_runner import TaskRunner, TaskRunnerError
from app.task_store import TaskStore
from app.workspace import WorkspaceError, WorkspaceManager

app = FastAPI(
    title="AgentDock API",
    description="Mobile-first command center for AI coding agents.",
    version="0.1.0",
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


def get_task_store(settings: Settings = Depends(get_settings)) -> TaskStore:
    """
    Return the JSON-backed task store.
    """

    return TaskStore(settings)


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


@app.post(
    "/tasks",
    response_model=TaskCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_task(
    payload: TaskCreateRequest,
    settings: Settings = Depends(get_settings),
    task_store: TaskStore = Depends(get_task_store),
) -> TaskCreateResponse:
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

    task = TaskSummary(
        task_id=task_id,
        repo_url=str(payload.repo_url),
        base_branch=payload.base_branch,
        working_branch=working_branch,
        instruction=payload.instruction,
        agent=payload.agent,
        status=TaskStatus.running,
        changed_files=[],
        created_at=now,
        updated_at=now,
    )

    task_store.save_task(task)

    try:
        runner = TaskRunner(settings)

        result = runner.run_task(
            task_id=task_id,
            repo_url=str(payload.repo_url),
            base_branch=payload.base_branch,
            working_branch=working_branch,
            instruction=payload.instruction,
            agent=payload.agent,
        )

        task.status = TaskStatus.waiting_for_approval
        task.changed_files = result.changed_files
        task.updated_at = datetime.now(timezone.utc)

        task_store.save_task(task)
        task_store.save_diff(task_id, result.diff)

        return TaskCreateResponse(
            task_id=task_id,
            status=task.status,
            message=result.message,
        )

    except TaskRunnerError as exc:
        task.status = TaskStatus.failed
        task.error = str(exc)
        task.updated_at = datetime.now(timezone.utc)

        task_store.save_task(task)
        task_store.save_diff(task_id, "")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "task_id": task_id,
                "error": str(exc),
            },
        ) from exc


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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace repository not found for task: {task_id}",
        )

    try:
        repo = GitHubClient(settings).open_repository(repo_path)
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

    return WorkspaceStatusResponse(
        task_id=task_id,
        repo_path=str(repo_path),
        branch=branch,
        head_commit=head_commit,
        is_dirty=repo.is_dirty(untracked_files=True),
        has_untracked_files=bool(repo.untracked_files),
        changed_files=changed_files,
        commit_sha=task.commit_sha,
        pushed=task.pushed,
        pr_url=task.pr_url,
    )


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
        task.status = TaskStatus.rejected
        task.updated_at = now
        task_store.save_task(task)

        return TaskApprovalResponse(
            task_id=task_id,
            status=task.status,
            message=payload.message or "Task rejected.",
        )

    if settings.allow_pr_creation and not settings.allow_push:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "task_id": task_id,
                "error": (
                    "PR creation requires ALLOW_PUSH=true because the branch "
                    "must exist on remote."
                ),
            },
        )

    try:
        workspace_manager = WorkspaceManager(settings)
        repo_path = workspace_manager.repo_path(task_id)
        github_client = GitHubClient(settings)
        repo = github_client.open_repository(repo_path)
        commit_sha = github_client.commit_all(repo, f"AgentDock task: {task_id}")
    except (GitHubClientError, WorkspaceError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "task_id": task_id,
                "error": str(exc),
            },
        ) from exc

    task.status = TaskStatus.completed
    task.commit_sha = commit_sha
    task.pushed = False
    task.pr_url = None
    task.updated_at = now

    if not settings.allow_push:
        task_store.save_task(task)

        return TaskApprovalResponse(
            task_id=task_id,
            status=task.status,
            message=f"Task approved and committed locally: {commit_sha}. Push is disabled.",
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
        github_client.push_branch(repo, task.working_branch)
        task.pushed = True
    except GitHubClientError as exc:
        task.pushed = False
        task.error = str(exc)
        task_store.save_task(task)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "task_id": task_id,
                "error": str(exc),
            },
        ) from exc

    if not settings.allow_pr_creation:
        task_store.save_task(task)

        return TaskApprovalResponse(
            task_id=task_id,
            status=task.status,
            message=(
                "Task approved, committed locally, and pushed branch "
                f"{task.working_branch}: {commit_sha}. PR creation is disabled."
            ),
        )

    try:
        pr_url = await github_client.create_pull_request(
            repo_url=task.repo_url,
            head_branch=task.working_branch,
            base_branch=task.base_branch,
            title=f"AgentDock task: {task_id}",
            body=(
                "Automated AgentDock task.\n\n"
                f"Instruction:\n{task.instruction}\n\n"
                f"Commit:\n{commit_sha}"
            ),
        )
    except GitHubClientError as exc:
        task.error = str(exc)
        task_store.save_task(task)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "task_id": task_id,
                "error": str(exc),
            },
        ) from exc

    task.pr_url = pr_url
    task_store.save_task(task)

    return TaskApprovalResponse(
        task_id=task_id,
        status=task.status,
        message=(
            "Task approved, committed locally, pushed branch "
            f"{task.working_branch}, and opened PR: {pr_url}"
        ),
    )
