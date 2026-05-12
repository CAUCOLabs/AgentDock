from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from app.config import Settings
from app.models import AgentProvider, ChangedFile, TaskStatus, TaskSummary
from app.task_store import TaskStore


class TaskStorePersistenceTests(unittest.TestCase):
    def test_task_logs_and_diff_persist_across_store_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))
            task = self._task("task_persist")
            task.workspace_path = str(settings.workspace_root / task.task_id / "repo")
            Path(task.workspace_path).mkdir(parents=True)
            task.changed_files = [
                ChangedFile(
                    path="README.md",
                    status="modified",
                    additions=1,
                    deletions=0,
                )
            ]

            store = TaskStore(settings)
            store.save_task(task)
            store.append_log(task.task_id, "info", "Task created.")
            store.save_diff(task.task_id, "diff --git a/README.md b/README.md")

            restarted_store = TaskStore(settings)
            persisted_task = restarted_store.get_task(task.task_id)

            self.assertIsNotNone(persisted_task)
            self.assertEqual(persisted_task.task_id, task.task_id)
            self.assertEqual(persisted_task.changed_files[0].path, "README.md")
            self.assertEqual(
                restarted_store.get_diff(task.task_id),
                "diff --git a/README.md b/README.md",
            )
            log_messages = [
                event.message for event in restarted_store.get_logs(task.task_id)
            ]
            self.assertIn("Task created.", log_messages)
            self.assertIn(
                "Task recovered after startup; still waiting for approval.",
                log_messages,
            )

    def test_completed_task_commit_sha_persists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))
            task = self._task("task_completed")
            task.status = TaskStatus.completed
            task.commit_sha = "abc123"
            task.completed_at = datetime.now(timezone.utc)
            task.pushed = True
            task.pushed_at = datetime.now(timezone.utc)
            task.remote_branch = "origin/agentdock/task_completed"
            task.last_push_result = "Push completed for branch agentdock/task_completed."

            TaskStore(settings).save_task(task)
            persisted_task = TaskStore(settings).get_task(task.task_id)

            self.assertIsNotNone(persisted_task)
            self.assertEqual(persisted_task.status, TaskStatus.completed)
            self.assertEqual(persisted_task.commit_sha, "abc123")
            self.assertIsNotNone(persisted_task.completed_at)
            self.assertTrue(persisted_task.pushed)
            self.assertIsNotNone(persisted_task.pushed_at)
            self.assertEqual(
                persisted_task.remote_branch,
                "origin/agentdock/task_completed",
            )
            self.assertIn("Push completed", persisted_task.last_push_result or "")

    def test_rejected_task_audit_data_and_clean_snapshot_persist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))
            task = self._task("task_rejected")
            task.status = TaskStatus.rejected
            task.rejected_at = datetime.now(timezone.utc)

            store = TaskStore(settings)
            store.save_task(task)
            store.save_diff(task.task_id, "preserved diff")
            store.append_log(task.task_id, "info", "Task rejected.")
            store.save_workspace_snapshot(
                task_id=task.task_id,
                repo_path=task.workspace_path or "",
                branch="agentdock/task_rejected",
                head_commit="abc123",
                is_dirty=False,
                has_untracked_files=False,
                changed_files=[],
            )

            restarted_store = TaskStore(settings)
            persisted_task = restarted_store.get_task(task.task_id)
            snapshot = restarted_store.get_workspace_status_snapshot(task.task_id)

            self.assertIsNotNone(persisted_task)
            self.assertEqual(persisted_task.status, TaskStatus.rejected)
            self.assertEqual(restarted_store.get_diff(task.task_id), "preserved diff")
            self.assertEqual(
                [event.message for event in restarted_store.get_logs(task.task_id)],
                ["Task rejected."],
            )
            self.assertIsNotNone(snapshot)
            self.assertFalse(snapshot.is_dirty)
            self.assertFalse(snapshot.has_untracked_files)

    def test_delete_hard_deletes_task_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))
            task = self._task("task_delete")

            store = TaskStore(settings)
            store.save_task(task)
            store.append_log(task.task_id, "info", "Task created.")
            store.save_diff(task.task_id, "diff")
            store.delete_task(task.task_id)

            restarted_store = TaskStore(settings)

            self.assertIsNone(restarted_store.get_task(task.task_id))
            self.assertEqual(restarted_store.get_diff(task.task_id), "")
            self.assertEqual(restarted_store.get_logs(task.task_id), [])

    def test_startup_recovery_marks_running_task_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))
            task = self._task("task_running")
            task.status = TaskStatus.running

            TaskStore(settings).save_task(task)
            restarted_store = TaskStore(settings)
            recovered_task = restarted_store.get_task(task.task_id)

            self.assertIsNotNone(recovered_task)
            self.assertEqual(recovered_task.status, TaskStatus.failed)
            self.assertEqual(
                recovered_task.error,
                "Task interrupted by backend restart.",
            )
            self.assertIn(
                "Task marked failed during startup recovery.",
                [event.message for event in restarted_store.get_logs(task.task_id)],
            )

    def _settings(self, temp_path: Path) -> Settings:
        return Settings(
            WORKSPACES_DIR=temp_path / "workspaces",
            LOGS_DIR=temp_path / "logs",
            AGENTDOCK_DB_PATH=temp_path / "data" / "agentdock.sqlite3",
        )

    def _task(self, task_id: str) -> TaskSummary:
        now = datetime.now(timezone.utc)
        return TaskSummary(
            task_id=task_id,
            repo_url="https://github.com/example/repo",
            repo_owner="example",
            repo_name="repo",
            base_branch="main",
            working_branch=f"agentdock/{task_id}",
            workspace_path=f"/tmp/{task_id}/repo",
            instruction="Persist this task for testing.",
            agent=AgentProvider.mock,
            status=TaskStatus.waiting_for_approval,
            changed_files=[],
            created_at=now,
            updated_at=now,
        )


if __name__ == "__main__":
    unittest.main()
