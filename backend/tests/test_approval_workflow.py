from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from git import Repo

from app.config import Settings, get_settings
from app.main import app
from app.models import AgentProvider, TaskStatus, TaskSummary
from app.task_store import TaskStore


class ApprovalWorkflowTests(unittest.TestCase):
    def test_approve_commits_workspace_without_push(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))
            task_id = "task_approvetest"
            repo_path = settings.workspace_root / task_id / "repo"
            repo = self._create_repo(repo_path)

            (repo_path / "approved.txt").write_text("approved\n", encoding="utf-8")
            store = self._save_waiting_task(settings, task_id)
            store.save_diff(task_id, "diff snapshot")

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                response = client.post(
                    f"/tasks/{task_id}/approval",
                    json={"approve": True},
                )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "completed")

            repo = Repo(repo_path)
            self.assertFalse(repo.is_dirty(untracked_files=True))
            self.assertFalse(repo.untracked_files)
            self.assertIn("AgentDock task: task_approvetest", repo.head.commit.message)

            task = TaskStore(settings).get_task(task_id)
            self.assertIsNotNone(task)
            self.assertIsNotNone(task.commit_sha)
            self.assertFalse(task.pushed)
            self.assertIn(
                "Push skipped because disabled.",
                [event.message for event in TaskStore(settings).get_logs(task_id)],
            )

    def test_workspace_status_includes_remote_readiness_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))
            task_id = "task_remotetest"
            repo_path = settings.workspace_root / task_id / "repo"
            repo = self._create_repo(repo_path)
            repo.create_remote("origin", "https://github.com/example/repo.git")
            task = self._save_waiting_task(settings, task_id).get_task(task_id)
            self.assertIsNotNone(task)
            task.status = TaskStatus.completed
            TaskStore(settings).save_task(task)

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                response = client.get(f"/tasks/{task_id}/workspace-status")
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(
                payload["remote_url"],
                "https://github.com/example/repo.git",
            )
            self.assertTrue(payload["has_valid_origin"])
            self.assertEqual(payload["default_branch"], "main")
            self.assertFalse(payload["push_enabled"])
            self.assertFalse(payload["pr_creation_enabled"])

    def test_reject_reverts_and_cleans_workspace_while_preserving_audit_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))
            task_id = "task_rejecttest"
            repo_path = settings.workspace_root / task_id / "repo"
            repo = self._create_repo(repo_path)

            (repo_path / "README.md").write_text("modified\n", encoding="utf-8")
            (repo_path / "scratch.txt").write_text("untracked\n", encoding="utf-8")

            store = self._save_waiting_task(settings, task_id)
            store.save_diff(task_id, "diff snapshot")

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                response = client.post(
                    f"/tasks/{task_id}/approval",
                    json={"approve": False, "message": "No"},
                )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "rejected")

            repo = Repo(repo_path)
            self.assertFalse(repo.is_dirty(untracked_files=True))
            self.assertFalse(repo.untracked_files)
            self.assertEqual(
                (repo_path / "README.md").read_text(encoding="utf-8"),
                "initial\n",
            )
            self.assertFalse((repo_path / "scratch.txt").exists())

            updated_store = TaskStore(settings)
            self.assertEqual(updated_store.get_diff(task_id), "diff snapshot")
            log_messages = [
                event.message for event in updated_store.get_logs(task_id)
            ]
            self.assertIn("Task rejected.", log_messages)
            self.assertIn("Workspace reverted.", log_messages)
            self.assertIn("Workspace cleaned.", log_messages)

    def test_delete_removes_task_and_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))
            task_id = "task_deletetest"
            repo_path = settings.workspace_root / task_id / "repo"
            self._create_repo(repo_path)
            self._save_waiting_task(settings, task_id)

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                response = client.delete(f"/tasks/{task_id}")
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "deleted")
            self.assertFalse((settings.workspace_root / task_id).exists())
            self.assertIsNone(TaskStore(settings).get_task(task_id))

    def test_approve_pushes_task_branch_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir), allow_push=True)
            task_id = "task_pushtest"
            branch = f"agentdock/{task_id}"
            remote_path = Path(temp_dir) / "origin.git"
            Repo.init(remote_path, bare=True)
            repo_path = settings.workspace_root / task_id / "repo"
            repo = self._create_repo(repo_path)
            repo.create_remote("origin", str(remote_path))
            repo.git.checkout("-b", branch)
            (repo_path / "pushed.txt").write_text("pushed\n", encoding="utf-8")
            store = self._save_waiting_task(settings, task_id)
            store.save_diff(task_id, "diff snapshot")

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                response = client.post(
                    f"/tasks/{task_id}/approval",
                    json={"approve": True},
                )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            task = TaskStore(settings).get_task(task_id)
            self.assertIsNotNone(task)
            self.assertTrue(task.pushed)
            self.assertIsNotNone(task.pushed_at)
            self.assertEqual(task.remote_branch, f"origin/{branch}")
            self.assertIn("Push completed", task.last_push_result or "")

            remote_repo = Repo(remote_path)
            self.assertIn(branch, [head.name for head in remote_repo.heads])

    def test_push_enabled_rejects_protected_branch_without_undoing_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir), allow_push=True)
            task_id = "task_mainpush"
            remote_path = Path(temp_dir) / "origin.git"
            Repo.init(remote_path, bare=True)
            repo_path = settings.workspace_root / task_id / "repo"
            repo = self._create_repo(repo_path)
            repo.create_remote("origin", str(remote_path))
            (repo_path / "protected.txt").write_text("commit only\n", encoding="utf-8")
            store = self._save_waiting_task(settings, task_id, branch="main")
            store.save_diff(task_id, "diff snapshot")

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                response = client.post(
                    f"/tasks/{task_id}/approval",
                    json={"approve": True},
                )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 500)
            repo = Repo(repo_path)
            self.assertIn("AgentDock task: task_mainpush", repo.head.commit.message)
            task = TaskStore(settings).get_task(task_id)
            self.assertIsNotNone(task)
            self.assertFalse(task.pushed)
            self.assertIn("protected branch", task.last_push_result or "")

    def test_push_enabled_missing_origin_fails_without_undoing_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir), allow_push=True)
            task_id = "task_noorigin"
            branch = f"agentdock/{task_id}"
            repo_path = settings.workspace_root / task_id / "repo"
            repo = self._create_repo(repo_path)
            repo.git.checkout("-b", branch)
            (repo_path / "missing-origin.txt").write_text("commit only\n", encoding="utf-8")
            store = self._save_waiting_task(settings, task_id)
            store.save_diff(task_id, "diff snapshot")

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                response = client.post(
                    f"/tasks/{task_id}/approval",
                    json={"approve": True},
                )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 500)
            repo = Repo(repo_path)
            self.assertIn("AgentDock task: task_noorigin", repo.head.commit.message)
            task = TaskStore(settings).get_task(task_id)
            self.assertIsNotNone(task)
            self.assertFalse(task.pushed)
            self.assertIn("origin remote is missing", task.last_push_result or "")

    def test_push_failure_persists_result_without_undoing_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir), allow_push=True)
            task_id = "task_pushfail"
            branch = f"agentdock/{task_id}"
            repo_path = settings.workspace_root / task_id / "repo"
            repo = self._create_repo(repo_path)
            repo.create_remote("origin", str(Path(temp_dir) / "missing.git"))
            repo.git.checkout("-b", branch)
            (repo_path / "push-fail.txt").write_text("commit only\n", encoding="utf-8")
            store = self._save_waiting_task(settings, task_id)
            store.save_diff(task_id, "diff snapshot")

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                response = client.post(
                    f"/tasks/{task_id}/approval",
                    json={"approve": True},
                )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 500)
            repo = Repo(repo_path)
            self.assertIn("AgentDock task: task_pushfail", repo.head.commit.message)
            restarted_store = TaskStore(settings)
            task = restarted_store.get_task(task_id)
            self.assertIsNotNone(task)
            self.assertFalse(task.pushed)
            self.assertEqual(task.remote_branch, f"origin/{branch}")
            self.assertIn("Push failed", task.last_push_result or "")
            self.assertIsNone(task.pushed_at)

    def _settings(self, temp_path: Path, allow_push: bool = False) -> Settings:
        return Settings(
            WORKSPACES_DIR=temp_path / "workspaces",
            LOGS_DIR=temp_path / "logs",
            AGENTDOCK_DB_PATH=temp_path / "data" / "agentdock.sqlite3",
            ALLOW_PUSH=allow_push,
        )

    def _save_waiting_task(
        self,
        settings: Settings,
        task_id: str,
        branch: str | None = None,
    ) -> TaskStore:
        store = TaskStore(settings)
        now = datetime.now(timezone.utc)
        store.save_task(
            TaskSummary(
                task_id=task_id,
                repo_url="https://github.com/example/repo",
                base_branch="main",
                working_branch=branch or f"agentdock/{task_id}",
                workspace_path=str(settings.workspace_root / task_id / "repo"),
                instruction="Modify the repository for testing.",
                agent=AgentProvider.mock,
                status=TaskStatus.waiting_for_approval,
                changed_files=[],
                created_at=now,
                updated_at=now,
            )
        )
        return store

    def _create_repo(self, repo_path: Path) -> Repo:
        repo_path.mkdir(parents=True, exist_ok=True)
        repo = Repo.init(repo_path, initial_branch="main")
        with repo.config_writer() as config:
            config.set_value("user", "name", "AgentDock Tests")
            config.set_value("user", "email", "agentdock-tests@example.local")
        (repo_path / "README.md").write_text("initial\n", encoding="utf-8")
        repo.git.add(A=True)
        repo.index.commit("initial")
        return repo


if __name__ == "__main__":
    unittest.main()
