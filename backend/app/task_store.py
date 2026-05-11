import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from pydantic import TypeAdapter

from app.config import Settings
from app.models import TaskLogEvent, TaskSummary


class TaskStoreError(Exception):
    """
    Raised when task persistence fails.
    """


class TaskStore:
    """
    Simple JSON-backed task store for the MVP.

    This keeps task metadata and diffs available even when the development
    server reloads. Later this can be replaced with SQLite/PostgreSQL.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.data_dir = settings.logs_root / "task_store"
        self.tasks_file = self.data_dir / "tasks.json"
        self.diffs_file = self.data_dir / "diffs.json"
        self.logs_file = self.data_dir / "logs.json"
        self._lock = Lock()
        self._task_list_adapter = TypeAdapter(list[TaskSummary])
        self._task_log_list_adapter = TypeAdapter(list[TaskLogEvent])

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_file(self.tasks_file, [])
        self._ensure_file(self.diffs_file, {})
        self._ensure_file(self.logs_file, {})

    def list_tasks(self) -> list[TaskSummary]:
        """
        Return all stored tasks.
        """

        with self._lock:
            raw_tasks = self._read_json(self.tasks_file)

        return self._task_list_adapter.validate_python(raw_tasks)

    def get_task(self, task_id: str) -> TaskSummary | None:
        """
        Return a task by id, or None if it does not exist.
        """

        tasks = self.list_tasks()

        for task in tasks:
            if task.task_id == task_id:
                return task

        return None

    def save_task(self, task: TaskSummary) -> None:
        """
        Create or update a task.
        """

        with self._lock:
            raw_tasks = self._read_json(self.tasks_file)
            tasks = self._task_list_adapter.validate_python(raw_tasks)

            updated = False

            for index, existing_task in enumerate(tasks):
                if existing_task.task_id == task.task_id:
                    tasks[index] = task
                    updated = True
                    break

            if not updated:
                tasks.append(task)

            serializable_tasks = [
                stored_task.model_dump(mode="json") for stored_task in tasks
            ]

            self._write_json(self.tasks_file, serializable_tasks)

    def delete_task(self, task_id: str) -> None:
        """
        Delete a task and its diff.
        """

        with self._lock:
            raw_tasks = self._read_json(self.tasks_file)
            tasks = self._task_list_adapter.validate_python(raw_tasks)

            remaining_tasks = [
                task for task in tasks if task.task_id != task_id
            ]

            self._write_json(
                self.tasks_file,
                [task.model_dump(mode="json") for task in remaining_tasks],
            )

            diffs = self._read_json(self.diffs_file)

            if task_id in diffs:
                del diffs[task_id]

            self._write_json(self.diffs_file, diffs)

            logs = self._read_json(self.logs_file)

            if task_id in logs:
                del logs[task_id]

            self._write_json(self.logs_file, logs)

    def get_diff(self, task_id: str) -> str:
        """
        Return the stored diff for a task.
        """

        with self._lock:
            diffs = self._read_json(self.diffs_file)

        return str(diffs.get(task_id, ""))

    def save_diff(self, task_id: str, diff: str) -> None:
        """
        Store or update a task diff.
        """

        with self._lock:
            diffs = self._read_json(self.diffs_file)
            diffs[task_id] = diff
            self._write_json(self.diffs_file, diffs)

    def append_log(self, task_id: str, level: str, message: str) -> None:
        """
        Append a timestamped task log event.
        """

        event = TaskLogEvent(
            timestamp=datetime.now(timezone.utc),
            level=level,
            message=message,
        )

        with self._lock:
            raw_logs = self._read_json(self.logs_file)
            existing_events = raw_logs.get(task_id, [])
            events = self._task_log_list_adapter.validate_python(existing_events)
            events.append(event)
            raw_logs[task_id] = [item.model_dump(mode="json") for item in events]
            self._write_json(self.logs_file, raw_logs)

    def get_logs(self, task_id: str) -> list[TaskLogEvent]:
        """
        Return stored task log events.
        """

        with self._lock:
            raw_logs = self._read_json(self.logs_file)

        return self._task_log_list_adapter.validate_python(raw_logs.get(task_id, []))

    def _ensure_file(self, path: Path, default_value: Any) -> None:
        """
        Create a JSON file with a default value if it does not exist.
        """

        if path.exists():
            return

        self._write_json(path, default_value)

    def _read_json(self, path: Path) -> Any:
        """
        Read JSON from disk.
        """

        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise TaskStoreError(f"Missing store file: {path}") from exc
        except json.JSONDecodeError as exc:
            raise TaskStoreError(f"Invalid JSON store file: {path}") from exc

    def _write_json(self, path: Path, data: Any) -> None:
        """
        Write JSON to disk atomically enough for local MVP usage.
        """

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_suffix(f"{path.suffix}.tmp")
            temp_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            temp_path.replace(path)
        except OSError as exc:
            raise TaskStoreError(f"Failed to write store file: {path}") from exc
