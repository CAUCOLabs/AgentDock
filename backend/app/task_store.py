import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse

from pydantic import TypeAdapter

from app.config import Settings
from app.models import (
    ChangedFile,
    TaskLogEvent,
    TaskStatus,
    TaskSummary,
    WorkspaceStatusResponse,
)


class TaskStoreError(Exception):
    """
    Raised when task persistence fails.
    """


class TaskStore:
    """
    SQLite-backed task store for MVP durability.

    The public methods intentionally match the earlier JSON-backed store so
    API handlers and clients keep the same behavior.
    """

    _lock = Lock()

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db_path = settings.db_path
        self._changed_files_adapter = TypeAdapter(list[ChangedFile])
        self._task_log_list_adapter = TypeAdapter(list[TaskLogEvent])

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()
        self._recover_interrupted_tasks()

    def list_tasks(self) -> list[TaskSummary]:
        """
        Return all stored, non-deleted tasks.
        """

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM tasks
                WHERE deleted_at IS NULL
                ORDER BY created_at ASC
                """
            ).fetchall()

        return [self._task_from_row(row) for row in rows]

    def get_task(self, task_id: str) -> TaskSummary | None:
        """
        Return a task by id, or None if it does not exist.
        """

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ? AND deleted_at IS NULL",
                (task_id,),
            ).fetchone()

        if row is None:
            return None

        return self._task_from_row(row)

    def save_task(self, task: TaskSummary) -> None:
        """
        Create or update a task.
        """

        repo_owner, repo_name = self._repo_owner_and_name(task)
        workspace_path = task.workspace_path or str(
            self.settings.workspace_root / task.task_id / "repo"
        )

        with self._connect() as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO tasks (
                        task_id,
                        repo_url,
                        repo_owner,
                        repo_name,
                        base_branch,
                        working_branch,
                        workspace_path,
                        target_file,
                        change_scope,
                        instruction,
                        agent,
                        status,
                        changed_files_json,
                        commit_sha,
                        pushed,
                        pushed_at,
                        remote_branch,
                        last_push_result,
                        pr_url,
                        created_at,
                        updated_at,
                        completed_at,
                        rejected_at,
                        deleted_at,
                        error
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        repo_url = excluded.repo_url,
                        repo_owner = excluded.repo_owner,
                        repo_name = excluded.repo_name,
                        base_branch = excluded.base_branch,
                        working_branch = excluded.working_branch,
                        workspace_path = excluded.workspace_path,
                        target_file = excluded.target_file,
                        change_scope = excluded.change_scope,
                        instruction = excluded.instruction,
                        agent = excluded.agent,
                        status = excluded.status,
                        changed_files_json = excluded.changed_files_json,
                        commit_sha = excluded.commit_sha,
                        pushed = excluded.pushed,
                        pushed_at = excluded.pushed_at,
                        remote_branch = excluded.remote_branch,
                        last_push_result = excluded.last_push_result,
                        pr_url = excluded.pr_url,
                        created_at = excluded.created_at,
                        updated_at = excluded.updated_at,
                        completed_at = excluded.completed_at,
                        rejected_at = excluded.rejected_at,
                        deleted_at = excluded.deleted_at,
                        error = excluded.error
                    """,
                    (
                        task.task_id,
                        task.repo_url,
                        repo_owner,
                        repo_name,
                        task.base_branch,
                        task.working_branch,
                        workspace_path,
                        task.target_file,
                        task.change_scope.value,
                        task.instruction,
                        task.agent.value,
                        task.status.value,
                        self._changed_files_json(task.changed_files),
                        task.commit_sha,
                        int(task.pushed),
                        self._datetime_to_text(task.pushed_at),
                        task.remote_branch,
                        task.last_push_result,
                        task.pr_url,
                        self._datetime_to_text(task.created_at),
                        self._datetime_to_text(task.updated_at),
                        self._datetime_to_text(task.completed_at),
                        self._datetime_to_text(task.rejected_at),
                        self._datetime_to_text(task.deleted_at),
                        task.error,
                    ),
                )

    def delete_task(self, task_id: str) -> None:
        """
        Hard-delete a task, its diff, logs, changed file metadata, and snapshots.
        """

        with self._connect() as connection:
            with connection:
                connection.execute(
                    "DELETE FROM workspace_snapshots WHERE task_id = ?",
                    (task_id,),
                )
                connection.execute(
                    "DELETE FROM task_diffs WHERE task_id = ?",
                    (task_id,),
                )
                connection.execute(
                    "DELETE FROM task_logs WHERE task_id = ?",
                    (task_id,),
                )
                connection.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))

    def get_diff(self, task_id: str) -> str:
        """
        Return the stored diff for a task.
        """

        with self._connect() as connection:
            row = connection.execute(
                "SELECT diff FROM task_diffs WHERE task_id = ?",
                (task_id,),
            ).fetchone()

        if row is None:
            return ""

        return str(row["diff"] or "")

    def save_diff(self, task_id: str, diff: str) -> None:
        """
        Store or update a task diff and changed file snapshot.
        """

        task = self.get_task(task_id)
        changed_files = task.changed_files if task is not None else []

        with self._connect() as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO task_diffs (
                        task_id,
                        diff,
                        changed_files_json,
                        generated_at
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        diff = excluded.diff,
                        changed_files_json = excluded.changed_files_json,
                        generated_at = excluded.generated_at
                    """,
                    (
                        task_id,
                        diff,
                        self._changed_files_json(changed_files),
                        self._datetime_to_text(datetime.now(timezone.utc)),
                    ),
                )

    def append_log(self, task_id: str, level: str, message: str) -> None:
        """
        Append a timestamped task log event.
        """

        with self._connect() as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO task_logs (task_id, timestamp, level, message)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        self._datetime_to_text(datetime.now(timezone.utc)),
                        level,
                        message,
                    ),
                )

    def get_logs(self, task_id: str) -> list[TaskLogEvent]:
        """
        Return stored task log events.
        """

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT timestamp, level, message
                FROM task_logs
                WHERE task_id = ?
                ORDER BY id ASC
                """,
                (task_id,),
            ).fetchall()

        raw_events = [
            {
                "timestamp": row["timestamp"],
                "level": row["level"],
                "message": row["message"],
            }
            for row in rows
        ]

        return self._task_log_list_adapter.validate_python(raw_events)

    def save_workspace_snapshot(
        self,
        task_id: str,
        repo_path: str,
        branch: str | None,
        head_commit: str | None,
        is_dirty: bool,
        has_untracked_files: bool,
        changed_files: list[ChangedFile],
        remote_url: str | None = None,
        has_valid_origin: bool = False,
        default_branch: str | None = None,
        push_enabled: bool = False,
        pr_creation_enabled: bool = False,
        commit_sha: str | None = None,
        pushed: bool = False,
        pushed_at: datetime | None = None,
        remote_branch: str | None = None,
        last_push_result: str | None = None,
        pr_url: str | None = None,
    ) -> None:
        """
        Persist the latest workspace status for audit after restarts.
        """

        with self._connect() as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO workspace_snapshots (
                        task_id,
                        repo_path,
                        branch,
                        head_commit,
                        remote_url,
                        has_valid_origin,
                        default_branch,
                        push_enabled,
                        pr_creation_enabled,
                        is_dirty,
                        has_untracked_files,
                        changed_files_json,
                        commit_sha,
                        pushed,
                        pushed_at,
                        remote_branch,
                        last_push_result,
                        pr_url,
                        captured_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        repo_path = excluded.repo_path,
                        branch = excluded.branch,
                        head_commit = excluded.head_commit,
                        remote_url = excluded.remote_url,
                        has_valid_origin = excluded.has_valid_origin,
                        default_branch = excluded.default_branch,
                        push_enabled = excluded.push_enabled,
                        pr_creation_enabled = excluded.pr_creation_enabled,
                        is_dirty = excluded.is_dirty,
                        has_untracked_files = excluded.has_untracked_files,
                        changed_files_json = excluded.changed_files_json,
                        commit_sha = excluded.commit_sha,
                        pushed = excluded.pushed,
                        pushed_at = excluded.pushed_at,
                        remote_branch = excluded.remote_branch,
                        last_push_result = excluded.last_push_result,
                        pr_url = excluded.pr_url,
                        captured_at = excluded.captured_at
                    """,
                    (
                        task_id,
                        repo_path,
                        branch,
                        head_commit,
                        remote_url,
                        int(has_valid_origin),
                        default_branch,
                        int(push_enabled),
                        int(pr_creation_enabled),
                        int(is_dirty),
                        int(has_untracked_files),
                        self._changed_files_json(changed_files),
                        commit_sha,
                        int(pushed),
                        self._datetime_to_text(pushed_at),
                        remote_branch,
                        last_push_result,
                        pr_url,
                        self._datetime_to_text(datetime.now(timezone.utc)),
                    ),
                )

    def get_workspace_snapshot(self, task_id: str) -> sqlite3.Row | None:
        """
        Return the last persisted workspace snapshot, if one exists.
        """

        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM workspace_snapshots WHERE task_id = ?",
                (task_id,),
            ).fetchone()

    def get_workspace_status_snapshot(
        self,
        task_id: str,
    ) -> WorkspaceStatusResponse | None:
        """
        Return the last persisted workspace snapshot as an API response model.
        """

        snapshot = self.get_workspace_snapshot(task_id)

        if snapshot is None:
            return None

        return WorkspaceStatusResponse(
            task_id=task_id,
            repo_path=str(snapshot["repo_path"]),
            branch=snapshot["branch"],
            head_commit=snapshot["head_commit"],
            remote_url=snapshot["remote_url"],
            has_valid_origin=bool(snapshot["has_valid_origin"]),
            default_branch=snapshot["default_branch"],
            push_enabled=bool(snapshot["push_enabled"]),
            pr_creation_enabled=bool(snapshot["pr_creation_enabled"]),
            is_dirty=bool(snapshot["is_dirty"]),
            has_untracked_files=bool(snapshot["has_untracked_files"]),
            changed_files=self._changed_files_from_json(snapshot["changed_files_json"]),
            commit_sha=snapshot["commit_sha"],
            pushed=bool(snapshot["pushed"]),
            pushed_at=snapshot["pushed_at"],
            remote_branch=snapshot["remote_branch"],
            last_push_result=snapshot["last_push_result"],
            pr_url=snapshot["pr_url"],
        )

    def _initialize_database(self) -> None:
        with TaskStore._lock:
            with self._connect() as connection:
                with connection:
                    connection.execute("PRAGMA foreign_keys = ON")
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS tasks (
                            task_id TEXT PRIMARY KEY,
                            repo_url TEXT NOT NULL,
                            repo_owner TEXT,
                            repo_name TEXT,
                            base_branch TEXT NOT NULL,
                            working_branch TEXT,
                            workspace_path TEXT,
                            target_file TEXT,
                            change_scope TEXT NOT NULL DEFAULT 'only_selected_file',
                            instruction TEXT NOT NULL,
                            agent TEXT NOT NULL,
                            status TEXT NOT NULL,
                            changed_files_json TEXT NOT NULL DEFAULT '[]',
                            commit_sha TEXT,
                            pushed INTEGER NOT NULL DEFAULT 0,
                            pushed_at TEXT,
                            remote_branch TEXT,
                            last_push_result TEXT,
                            pr_url TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            completed_at TEXT,
                            rejected_at TEXT,
                            deleted_at TEXT,
                            error TEXT
                        );

                        CREATE TABLE IF NOT EXISTS task_logs (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            task_id TEXT NOT NULL,
                            timestamp TEXT NOT NULL,
                            level TEXT NOT NULL,
                            message TEXT NOT NULL,
                            FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                                ON DELETE CASCADE
                        );

                        CREATE INDEX IF NOT EXISTS idx_task_logs_task_id_id
                        ON task_logs(task_id, id);

                        CREATE TABLE IF NOT EXISTS task_diffs (
                            task_id TEXT PRIMARY KEY,
                            diff TEXT NOT NULL,
                            changed_files_json TEXT NOT NULL DEFAULT '[]',
                            generated_at TEXT NOT NULL,
                            FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                                ON DELETE CASCADE
                        );

                        CREATE TABLE IF NOT EXISTS workspace_snapshots (
                            task_id TEXT PRIMARY KEY,
                            repo_path TEXT NOT NULL,
                            branch TEXT,
                            head_commit TEXT,
                            remote_url TEXT,
                            has_valid_origin INTEGER NOT NULL DEFAULT 0,
                            default_branch TEXT,
                            push_enabled INTEGER NOT NULL DEFAULT 0,
                            pr_creation_enabled INTEGER NOT NULL DEFAULT 0,
                            is_dirty INTEGER NOT NULL,
                            has_untracked_files INTEGER NOT NULL,
                            changed_files_json TEXT NOT NULL DEFAULT '[]',
                            commit_sha TEXT,
                            pushed INTEGER NOT NULL DEFAULT 0,
                            pushed_at TEXT,
                            remote_branch TEXT,
                            last_push_result TEXT,
                            pr_url TEXT,
                            captured_at TEXT NOT NULL,
                            FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                                ON DELETE CASCADE
                        );
                        """
                    )
                    self._ensure_workspace_snapshot_columns(connection)
                    self._ensure_task_columns(connection)

    def _recover_interrupted_tasks(self) -> None:
        """
        Mark interrupted active runs conservatively after process restart.
        """

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT task_id, status, workspace_path
                FROM tasks
                WHERE deleted_at IS NULL
                  AND status IN (?, ?)
                """,
                (TaskStatus.running.value, TaskStatus.waiting_for_approval.value),
            ).fetchall()

            with connection:
                for row in rows:
                    task_id = row["task_id"]
                    status = row["status"]

                    if status == TaskStatus.running.value:
                        self._update_recovered_task(
                            connection,
                            task_id,
                            TaskStatus.failed,
                            "Task interrupted by backend restart.",
                            "Task marked failed during startup recovery.",
                        )
                        continue

                    if not self._waiting_task_has_review_data(connection, row):
                        self._update_recovered_task(
                            connection,
                            task_id,
                            TaskStatus.failed,
                            "Task lost review data during backend restart.",
                            "Task marked failed during startup recovery.",
                        )
                        continue

                    self._append_recovery_log_once(
                        connection,
                        task_id,
                        "Task recovered after startup; still waiting for approval.",
                    )

    def _waiting_task_has_review_data(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> bool:
        diff_row = connection.execute(
            "SELECT diff FROM task_diffs WHERE task_id = ?",
            (row["task_id"],),
        ).fetchone()

        if diff_row is None or not str(diff_row["diff"] or "").strip():
            return False

        workspace_path = row["workspace_path"]

        if not workspace_path:
            return False

        return Path(workspace_path).exists()

    def _update_recovered_task(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        status: TaskStatus,
        error: str,
        message: str,
    ) -> None:
        now = self._datetime_to_text(datetime.now(timezone.utc))
        connection.execute(
            """
            UPDATE tasks
            SET status = ?, error = ?, updated_at = ?
            WHERE task_id = ?
            """,
            (status.value, error, now, task_id),
        )
        self._append_recovery_log_once(connection, task_id, message)

    def _append_recovery_log_once(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        message: str,
    ) -> None:
        existing = connection.execute(
            """
            SELECT 1 FROM task_logs
            WHERE task_id = ? AND message = ?
            LIMIT 1
            """,
            (task_id, message),
        ).fetchone()

        if existing is not None:
            return

        connection.execute(
            """
            INSERT INTO task_logs (task_id, timestamp, level, message)
            VALUES (?, ?, ?, ?)
            """,
            (
                task_id,
                self._datetime_to_text(datetime.now(timezone.utc)),
                "warning",
                message,
            ),
        )

    def _task_from_row(self, row: sqlite3.Row) -> TaskSummary:
        raw_task = {
            "task_id": row["task_id"],
            "repo_url": row["repo_url"],
            "repo_owner": row["repo_owner"],
            "repo_name": row["repo_name"],
            "base_branch": row["base_branch"],
            "working_branch": row["working_branch"],
            "workspace_path": row["workspace_path"],
            "target_file": row["target_file"],
            "change_scope": row["change_scope"],
            "instruction": row["instruction"],
            "agent": row["agent"],
            "status": row["status"],
            "changed_files": self._changed_files_from_json(row["changed_files_json"]),
            "commit_sha": row["commit_sha"],
            "pushed": bool(row["pushed"]),
            "pushed_at": row["pushed_at"],
            "remote_branch": row["remote_branch"],
            "last_push_result": row["last_push_result"],
            "pr_url": row["pr_url"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
            "rejected_at": row["rejected_at"],
            "deleted_at": row["deleted_at"],
            "error": row["error"],
        }

        return TaskSummary.model_validate(raw_task)

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.db_path)
        except sqlite3.Error as exc:
            raise TaskStoreError(f"Failed to open task database: {self.db_path}") from exc

        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _ensure_workspace_snapshot_columns(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(workspace_snapshots)")
        }
        desired_columns = {
            "remote_url": "TEXT",
            "has_valid_origin": "INTEGER NOT NULL DEFAULT 0",
            "default_branch": "TEXT",
            "push_enabled": "INTEGER NOT NULL DEFAULT 0",
            "pr_creation_enabled": "INTEGER NOT NULL DEFAULT 0",
            "pushed_at": "TEXT",
            "remote_branch": "TEXT",
            "last_push_result": "TEXT",
            "target_file": "TEXT",
        }

        for column_name, column_type in desired_columns.items():
            if column_name in columns:
                continue

            connection.execute(
                f"ALTER TABLE workspace_snapshots ADD COLUMN {column_name} {column_type}"
            )

    def _ensure_task_columns(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(tasks)")
        }
        desired_columns = {
            "pushed_at": "TEXT",
            "remote_branch": "TEXT",
            "last_push_result": "TEXT",
            "change_scope": "TEXT NOT NULL DEFAULT 'only_selected_file'",
        }

        for column_name, column_type in desired_columns.items():
            if column_name in columns:
                continue

            connection.execute(
                f"ALTER TABLE tasks ADD COLUMN {column_name} {column_type}"
            )

    def _repo_owner_and_name(self, task: TaskSummary) -> tuple[str | None, str | None]:
        if task.repo_owner and task.repo_name:
            return task.repo_owner, task.repo_name

        parsed = urlparse(task.repo_url)
        path_parts = parsed.path.strip("/").split("/")

        if len(path_parts) >= 2:
            return path_parts[0], path_parts[1].removesuffix(".git")

        if parsed.scheme == "" and len(Path(task.repo_url).parts) >= 1:
            return None, Path(task.repo_url).name

        return task.repo_owner, task.repo_name

    def _changed_files_json(self, changed_files: list[ChangedFile]) -> str:
        return json.dumps(
            [item.model_dump(mode="json") for item in changed_files],
            ensure_ascii=False,
        )

    def _changed_files_from_json(self, raw_value: str | None) -> list[ChangedFile]:
        if not raw_value:
            return []

        try:
            raw_files = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise TaskStoreError("Invalid changed file metadata in database.") from exc

        return self._changed_files_adapter.validate_python(raw_files)

    def _datetime_to_text(self, value: datetime | None) -> str | None:
        if value is None:
            return None

        return value.isoformat()
