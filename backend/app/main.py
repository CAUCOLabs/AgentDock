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
    TaskLogsResponse,
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
    Return the SQLite-backed task store.
    """

    return TaskStore(settings)


@app.on_event("startup")
def initialize_task_store() -> None:
    """
    Initialize SQLite storage and run conservative task recovery at startup.
    """

    TaskStore(get_settings())


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
        workspace_path=str(settings.workspace_root / task_id / "repo"),
        instruction=payload.instruction,
        agent=payload.agent,
        status=TaskStatus.running,
        changed_files=[],
        created_at=now,
        updated_at=now,
    )

    task_store.save_task(task)
    task_store.append_log(task_id, "info", "Task created.")
    task_store.append_log(task_id, "info", "Task running.")

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
        task.status = TaskStatus.failed
        task.error = str(exc)
        task.updated_at = datetime.now(timezone.utc)

        task_store.save_task(task)
        task_store.save_diff(task_id, "")
        for level, message in exc.log_messages:
            task_store.append_log(task_id, level, message)
        task_store.append_log(task_id, "error", f"Task runner failed: {exc}")

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
        pr_url=task.pr_url,
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

    task.status = TaskStatus.completed
    task.commit_sha = commit_sha
    task.pushed = False
    task.pushed_at = None
    task.remote_branch = None
    task.last_push_result = "Push not attempted."
    task.pr_url = None
    task.updated_at = now
    task.completed_at = now
    task_store.append_log(task_id, "info", f"Task locally committed: {commit_sha}.")

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
            commit_sha=commit_sha,
            pushed=False,
            pushed_at=None,
            remote_branch=None,
            last_push_result=task.last_push_result,
            pr_url=None,
        )
    except DiffServiceError as exc:
        task_store.append_log(
            task_id,
            "warning",
            f"Workspace snapshot failed after approval: {exc}",
        )

    if not settings.allow_push:
        task_store.save_task(task)
        task_store.append_log(task_id, "info", "Push skipped because disabled.")

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

        task.pushed = True
        task.pushed_at = datetime.now(timezone.utc)
        task_store.append_log(
            task_id,
            "info",
            f"Push completed for branch {task.working_branch}.",
        )
    except GitHubClientError as exc:
        task.pushed = False
        task.error = str(exc)
        task.last_push_result = str(exc)
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
            commit_sha=commit_sha,
            pushed=task.pushed,
            pushed_at=task.pushed_at,
            remote_branch=task.remote_branch,
            last_push_result=task.last_push_result,
            pr_url=task.pr_url,
        )
    except DiffServiceError as exc:
        task_store.append_log(
            task_id,
            "warning",
            f"Workspace snapshot failed after push: {exc}",
        )

    if not settings.allow_pr_creation:
        task_store.save_task(task)
        task_store.append_log(task_id, "info", "PR skipped because disabled.")

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
        task_store.append_log(task_id, "error", f"PR creation failed: {exc}")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "task_id": task_id,
                "error": str(exc),
            },
        ) from exc

    task.pr_url = pr_url
    task_store.save_task(task)
    task_store.append_log(task_id, "info", f"PR created: {pr_url}")

    return TaskApprovalResponse(
        task_id=task_id,
        status=task.status,
        message=(
            "Task approved, committed locally, pushed branch "
            f"{task.working_branch}, and opened PR: {pr_url}"
        ),
    )
