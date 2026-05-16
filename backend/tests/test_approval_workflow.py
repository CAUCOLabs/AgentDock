from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from git import Repo

from app.config import Settings, get_settings
from app.github_client import GitHubClientError, GitPullRequestResult
from app.main import app
from app.models import AgentProvider, TaskStatus, TaskSummary
from app.task_store import TaskStore


class ApprovalWorkflowTests(unittest.TestCase):
    def test_commit_only_flow_commits_workspace_without_push(self) -> None:
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
            self.assertEqual(response.json()["status"], "committed")

            repo = Repo(repo_path)
            self.assertFalse(repo.is_dirty(untracked_files=True))
            self.assertFalse(repo.untracked_files)
            self.assertIn("AgentDock task: task_approvetest", repo.head.commit.message)

            task = TaskStore(settings).get_task(task_id)
            self.assertIsNotNone(task)
            self.assertIsNotNone(task.commit_sha)
            self.assertFalse(task.pushed)
            self.assertIn(
                "Push unavailable because ALLOW_PUSH=false.",
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
            task.status = TaskStatus.committed
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

    def test_push_enabled_after_commit_pushes_task_branch(self) -> None:
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
                push_response = client.post(f"/tasks/{task_id}/push")
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "committed")
            self.assertEqual(push_response.status_code, 200)
            self.assertEqual(push_response.json()["status"], "pushed")
            task = TaskStore(settings).get_task(task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.status, TaskStatus.pushed)
            self.assertTrue(task.pushed)
            self.assertIsNotNone(task.pushed_at)
            self.assertEqual(task.remote_branch, f"origin/{branch}")
            self.assertIn("Push completed", task.last_push_result or "")
            self.assertEqual(task.branch_url, f"https://github.com/example/repo/tree/{branch}")

            remote_repo = Repo(remote_path)
            self.assertIn(branch, [head.name for head in remote_repo.heads])

    def test_pr_disabled_keeps_pushed_task_with_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir), allow_push=True, allow_pr_creation=False)
            task_id = "task_prdisabled"
            self._save_pushed_task(settings, task_id)

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                response = client.post(f"/tasks/{task_id}/pull-request")
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 403)
            task = TaskStore(settings).get_task(task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.status, TaskStatus.pushed)
            self.assertIn("ALLOW_PR_CREATION=true", task.error or "")

    def test_pr_success_saves_url_number_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir), allow_push=True, allow_pr_creation=True)
            task_id = "task_prsuccess"
            self._save_pushed_task(settings, task_id)

            async def fake_create_pull_request(*args, **kwargs):
                self.assertEqual(kwargs["title"], f"AgentDock task: {task_id}")
                self.assertIn("Task prompt:", kwargs["body"])
                self.assertIn("Target file:", kwargs["body"])
                self.assertIn("Agent provider:", kwargs["body"])
                self.assertIn("Commit SHA:", kwargs["body"])
                return GitPullRequestResult(
                    url="https://github.com/example/repo/pull/7",
                    number=7,
                    state="open",
                )

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                with patch(
                    "app.github_client.GitHubClient.create_pull_request",
                    side_effect=fake_create_pull_request,
                ):
                    response = client.post(f"/tasks/{task_id}/pull-request")
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "pull_request_opened")
            task = TaskStore(settings).get_task(task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.status, TaskStatus.pull_request_opened)
            self.assertEqual(task.pr_url, "https://github.com/example/repo/pull/7")
            self.assertEqual(task.pr_number, 7)
            self.assertEqual(task.pr_state, "open")

    def test_pr_failure_keeps_pushed_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir), allow_push=True, allow_pr_creation=True)
            task_id = "task_prfail"
            self._save_pushed_task(settings, task_id)

            async def fake_create_pull_request(*args, **kwargs):
                raise GitHubClientError("GitHub API denied the pull request.")

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                with patch(
                    "app.github_client.GitHubClient.create_pull_request",
                    side_effect=fake_create_pull_request,
                ):
                    response = client.post(f"/tasks/{task_id}/pull-request")
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 500)
            task = TaskStore(settings).get_task(task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.status, TaskStatus.pushed)
            self.assertTrue(task.pushed)
            self.assertIsNone(task.pr_url)
            self.assertIn("GitHub API denied", task.error or "")

    def test_create_pr_only_available_after_pushed_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir), allow_push=True, allow_pr_creation=True)
            task_id = "task_notpushed"
            store = self._save_waiting_task(settings, task_id)
            task = store.get_task(task_id)
            self.assertIsNotNone(task)
            task.status = TaskStatus.committed
            task.commit_sha = "abc123"
            store.save_task(task)

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                response = client.post(f"/tasks/{task_id}/pull-request")
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 409)

    def test_pr_creation_with_github_app_token_succeeds_without_github_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                allow_push=True,
                allow_pr_creation=True,
                github_token=None,
            )
            task_id = "task_apppr"
            self._save_pushed_task(
                settings,
                task_id,
                remote_token="ghs_installation_token",
            )

            async def fake_create_pull_request(client, *args, **kwargs):
                self.assertEqual(client.github_token, "ghs_installation_token")
                return GitPullRequestResult(
                    url="https://github.com/example/repo/pull/8",
                    number=8,
                    state="open",
                )

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                with patch(
                    "app.github_client.GitHubClient.create_pull_request",
                    autospec=True,
                    side_effect=fake_create_pull_request,
                ):
                    response = client.post(f"/tasks/{task_id}/pull-request")
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            task = TaskStore(settings).get_task(task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.pr_number, 8)

    def test_pr_creation_falls_back_to_github_token_for_manual_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                allow_push=True,
                allow_pr_creation=True,
                github_token="ghp_manual_token",
            )
            task_id = "task_manualpr"
            self._save_pushed_task(settings, task_id)

            async def fake_create_pull_request(client, *args, **kwargs):
                self.assertEqual(client.github_token, "ghp_manual_token")
                return GitPullRequestResult(
                    url="https://github.com/example/repo/pull/9",
                    number=9,
                    state="open",
                )

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                with patch(
                    "app.github_client.GitHubClient.create_pull_request",
                    autospec=True,
                    side_effect=fake_create_pull_request,
                ):
                    response = client.post(f"/tasks/{task_id}/pull-request")
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            task = TaskStore(settings).get_task(task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.pr_number, 9)

    def test_pr_permission_failure_returns_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                allow_push=True,
                allow_pr_creation=True,
                github_token=None,
            )
            task_id = "task_prpermissions"
            self._save_pushed_task(
                settings,
                task_id,
                remote_token="ghs_read_only_token",
            )

            async def fake_create_pull_request(client, *args, **kwargs):
                self.assertEqual(client.github_token, "ghs_read_only_token")
                raise GitHubClientError(
                    "GitHub App needs pull request write permission."
                )

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                with patch(
                    "app.github_client.GitHubClient.create_pull_request",
                    autospec=True,
                    side_effect=fake_create_pull_request,
                ):
                    response = client.post(f"/tasks/{task_id}/pull-request")
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 500)
            self.assertEqual(
                response.json()["detail"]["error"],
                "GitHub App needs pull request write permission.",
            )
            task = TaskStore(settings).get_task(task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.status, TaskStatus.pushed)
            self.assertEqual(
                task.error,
                "GitHub App needs pull request write permission.",
            )

    def test_push_failure_keeps_committed_task_for_protected_branch(self) -> None:
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
                push_response = client.post(f"/tasks/{task_id}/push")
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            self.assertEqual(push_response.status_code, 500)
            repo = Repo(repo_path)
            self.assertIn("AgentDock task: task_mainpush", repo.head.commit.message)
            task = TaskStore(settings).get_task(task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.status, TaskStatus.committed)
            self.assertFalse(task.pushed)
            self.assertIn("protected branch", task.last_push_result or "")

    def test_push_disabled_keeps_committed_task_with_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir), allow_push=False)
            task_id = "task_pushdisabled"
            branch = f"agentdock/{task_id}"
            repo_path = settings.workspace_root / task_id / "repo"
            repo = self._create_repo(repo_path)
            repo.git.checkout("-b", branch)
            (repo_path / "push-disabled.txt").write_text("commit only\n", encoding="utf-8")
            store = self._save_waiting_task(settings, task_id)
            store.save_diff(task_id, "diff snapshot")

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                response = client.post(
                    f"/tasks/{task_id}/approval",
                    json={"approve": True},
                )
                push_response = client.post(f"/tasks/{task_id}/push")
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            self.assertEqual(push_response.status_code, 403)
            task = TaskStore(settings).get_task(task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.status, TaskStatus.committed)
            self.assertFalse(task.pushed)
            self.assertIn("ALLOW_PUSH=true", task.last_push_result or "")

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
                push_response = client.post(f"/tasks/{task_id}/push")
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            self.assertEqual(push_response.status_code, 500)
            repo = Repo(repo_path)
            self.assertIn("AgentDock task: task_noorigin", repo.head.commit.message)
            task = TaskStore(settings).get_task(task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.status, TaskStatus.committed)
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
                push_response = client.post(f"/tasks/{task_id}/push")
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            self.assertEqual(push_response.status_code, 500)
            repo = Repo(repo_path)
            self.assertIn("AgentDock task: task_pushfail", repo.head.commit.message)
            restarted_store = TaskStore(settings)
            task = restarted_store.get_task(task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.status, TaskStatus.committed)
            self.assertFalse(task.pushed)
            self.assertEqual(task.remote_branch, f"origin/{branch}")
            self.assertIn("Push failed", task.last_push_result or "")
            self.assertIsNone(task.pushed_at)

    def test_ui_does_not_show_approve_text(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        web_app = (repo_root / "frontend" / "src" / "App.tsx").read_text()
        mobile_app = (repo_root / "mobile" / "App.tsx").read_text()

        self.assertNotIn("Approve", web_app)
        self.assertNotIn("Approve", mobile_app)

    def test_primary_ui_is_codex_only(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        web_app = (repo_root / "frontend" / "src" / "App.tsx").read_text()
        mobile_app = (repo_root / "mobile" / "App.tsx").read_text()

        for source in (web_app, mobile_app):
            self.assertIn("OpenAI Codex", source)
            self.assertIn("AgentDock uses OpenAI Codex to prepare code changes.", source)
            self.assertNotIn("Claude", source)
            self.assertNotIn("Ollama", source)
            self.assertNotIn("Local model", source)
            self.assertNotIn("Choose the agent profile for this task.", source)

    def test_task_launch_ui_pins_codex_agent(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        web_app = (repo_root / "frontend" / "src" / "App.tsx").read_text()
        mobile_app = (repo_root / "mobile" / "App.tsx").read_text()

        self.assertIn("agent: 'codex'", web_app)
        self.assertIn('agent: "codex"', mobile_app)
        self.assertNotIn("agent: form.agent", web_app)
        self.assertNotIn("agent: agentProvider", mobile_app)

    def _settings(
        self,
        temp_path: Path,
        allow_push: bool = False,
        allow_pr_creation: bool = False,
        github_token: str | None = None,
    ) -> Settings:
        return Settings(
            WORKSPACES_DIR=temp_path / "workspaces",
            LOGS_DIR=temp_path / "logs",
            AGENTDOCK_DB_PATH=temp_path / "data" / "agentdock.sqlite3",
            ALLOW_PUSH=allow_push,
            ALLOW_PR_CREATION=allow_pr_creation,
            GITHUB_TOKEN=github_token,
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

    def _save_pushed_task(
        self,
        settings: Settings,
        task_id: str,
        remote_token: str | None = None,
    ) -> TaskStore:
        store = TaskStore(settings)
        now = datetime.now(timezone.utc)
        branch = f"agentdock/{task_id}"
        repo_path = settings.workspace_root / task_id / "repo"
        repo = self._create_repo(repo_path)
        repo.git.checkout("-b", branch)
        if remote_token:
            repo.create_remote(
                "origin",
                f"https://x-access-token:{remote_token}@github.com/example/repo.git",
            )
        store.save_task(
            TaskSummary(
                task_id=task_id,
                repo_url="https://github.com/example/repo",
                base_branch="main",
                working_branch=branch,
                workspace_path=str(repo_path),
                instruction="Modify the repository for testing.",
                target_file="src/App.tsx",
                agent=AgentProvider.mock,
                status=TaskStatus.pushed,
                changed_files=[],
                commit_sha="abc123",
                pushed=True,
                pushed_at=now,
                remote_branch=f"origin/{branch}",
                last_push_result=f"Push completed for branch {branch}.",
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
