from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from git import Repo

from app.agent_providers import (
    CODEX_CHATGPT_OAUTH_AUTH_MODE,
    AgentProviderConnectionStore,
)
from app.config import Settings, get_settings
from app.main import app
from app.task_store import TaskStore


class TaskCreationErrorTests(unittest.TestCase):
    def test_provider_execution_exception_logs_task_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_repo = self._create_repo(Path(temp_dir) / "source")
            settings = self._settings(Path(temp_dir))
            AgentProviderConnectionStore(settings).connect(
                "codex",
                CODEX_CHATGPT_OAUTH_AUTH_MODE,
            )

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                with self.assertLogs(level="ERROR") as logs, patch(
                    "app.agent_runner.shutil.which",
                    return_value="/usr/bin/codex",
                ), patch(
                    "app.agent_runner.CodexCliProvider.run",
                    side_effect=RuntimeError("provider exploded token=secret"),
                ):
                    response = client.post(
                        "/tasks",
                        json=self._payload(source_repo),
                    )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 500)
            payload = response.json()
            self.assertEqual(payload["error_type"], "RuntimeError")
            self.assertEqual(payload["provider"], "codex")
            self.assertIn("[REDACTED]", payload["detail"])
            self.assertNotIn("secret", payload["detail"])
            self.assertTrue(
                any("Unexpected task runner failure" in log for log in logs.output)
            )
            self.assertTrue(
                any("Task creation runner failed" in log for log in logs.output)
            )

            task = TaskStore(settings).get_task(payload["task_id"])
            self.assertIsNotNone(task)
            self.assertEqual(task.status.value, "failed")
            logs = [event.message for event in TaskStore(settings).get_logs(task.task_id)]
            self.assertTrue(any("Unexpected task runner error" in log for log in logs))
            self.assertTrue(any("Task runner failed" in log for log in logs))

    def test_post_tasks_returns_useful_error_json_for_codex_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_repo = self._create_repo(Path(temp_dir) / "source")
            settings = self._settings(Path(temp_dir))
            AgentProviderConnectionStore(settings).connect(
                "codex",
                CODEX_CHATGPT_OAUTH_AUTH_MODE,
            )
            completed = subprocess.CompletedProcess(
                args=["codex"],
                returncode=12,
                stdout="codex stdout",
                stderr="codex stderr",
            )

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                with self.assertLogs(level="ERROR"), patch(
                    "app.agent_runner.shutil.which",
                    return_value="/usr/bin/codex",
                ), patch("app.agent_runner.subprocess.run", return_value=completed):
                    response = client.post(
                        "/tasks",
                        json=self._payload(source_repo),
                    )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 500)
            payload = response.json()
            self.assertEqual(
                set(payload),
                {"detail", "error_type", "provider", "task_id"},
            )
            self.assertEqual(payload["detail"], "Codex CLI failed.")
            self.assertEqual(payload["error_type"], "ProviderExecutionError")
            self.assertEqual(payload["provider"], "codex")
            self.assertTrue(payload["task_id"])
            logs = [
                event.message
                for event in TaskStore(settings).get_logs(payload["task_id"])
            ]
            self.assertTrue(any("Runner error: Codex CLI failed." in log for log in logs))
            self.assertTrue(any("Runner exit code: 12." in log for log in logs))
            self.assertTrue(any("codex stdout" in log for log in logs))
            self.assertTrue(any("codex stderr" in log for log in logs))

    def test_failed_codex_start_stores_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_repo = self._create_repo(Path(temp_dir) / "source")
            settings = self._settings(Path(temp_dir))
            AgentProviderConnectionStore(settings).connect(
                "codex",
                CODEX_CHATGPT_OAUTH_AUTH_MODE,
            )

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                with self.assertLogs(level="ERROR"), patch(
                    "app.agent_runner.shutil.which",
                    return_value="/usr/bin/codex",
                ), patch(
                    "app.agent_runner.subprocess.run",
                    side_effect=OSError("cannot exec codex"),
                ):
                    response = client.post(
                        "/tasks",
                        json=self._payload(source_repo),
                    )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 500)
            task_id = response.json()["task_id"]
            store = TaskStore(settings)
            task = store.get_task(task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.status.value, "failed")
            self.assertEqual(task.error, "Codex CLI failed to start: cannot exec codex")
            logs = [event.message for event in store.get_logs(task_id)]
            self.assertTrue(
                any("Runner error: Codex CLI failed to start" in log for log in logs)
            )
            self.assertTrue(any("Runner stderr:" in log for log in logs))
            self.assertTrue(any("cannot exec codex" in log for log in logs))

    def test_codex_task_success_produces_diff_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_repo = self._create_repo(Path(temp_dir) / "source")
            settings = self._settings(Path(temp_dir))
            AgentProviderConnectionStore(settings).connect(
                "codex",
                CODEX_CHATGPT_OAUTH_AUTH_MODE,
            )

            def run_codex(
                args: list[str],
                cwd: Path,
                capture_output: bool,
                text: bool,
                timeout: int,
                check: bool,
            ) -> subprocess.CompletedProcess[str]:
                self.assertNotIn("--ask-for-approval", args)
                (cwd / "src" / "App.tsx").write_text(
                    "export function App() { return 'real codex change'; }\n",
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout="codex changed src/App.tsx",
                    stderr="",
                )

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                with (
                    patch("app.agent_runner.shutil.which", return_value="/usr/bin/codex"),
                    patch("app.agent_runner.subprocess.run", side_effect=run_codex),
                ):
                    response = client.post(
                        "/tasks",
                        json=self._payload(source_repo),
                    )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 201)
            payload = response.json()
            self.assertEqual(payload["status"], "waiting_for_approval")
            store = TaskStore(settings)
            task = store.get_task(payload["task_id"])
            self.assertIsNotNone(task)
            self.assertEqual(task.status.value, "waiting_for_approval")
            diff = store.get_diff(payload["task_id"])
            self.assertIn("real codex change", diff)
            logs = [event.message for event in store.get_logs(payload["task_id"])]
            self.assertTrue(any("codex changed src/App.tsx" in log for log in logs))

    def _settings(self, temp_path: Path) -> Settings:
        return Settings(
            WORKSPACES_DIR=temp_path / "workspaces",
            LOGS_DIR=temp_path / "logs",
            AGENTDOCK_DB_PATH=temp_path / "data" / "agentdock.sqlite3",
            CODEX_ENABLED=True,
            CODEX_TIMEOUT_SECONDS=5,
            OPENAI_API_KEY="openai-key",
        )

    def _payload(self, source_repo: Repo) -> dict[str, str]:
        return {
            "repo_url": str(source_repo.working_tree_dir),
            "base_branch": "main",
            "instruction": "Update the selected component safely.",
            "agent": "codex",
            "target_file": "src/App.tsx",
            "change_scope": "only_selected_file",
        }

    def _create_repo(self, repo_path: Path) -> Repo:
        repo_path.mkdir(parents=True, exist_ok=True)
        repo = Repo.init(repo_path, initial_branch="main")
        with repo.config_writer() as config:
            config.set_value("user", "name", "AgentDock Tests")
            config.set_value("user", "email", "agentdock-tests@example.local")
        (repo_path / "src").mkdir()
        (repo_path / "src" / "App.tsx").write_text(
            "export function App() { return null; }\n",
            encoding="utf-8",
        )
        repo.git.add(A=True)
        repo.index.commit("initial")
        return repo


if __name__ == "__main__":
    unittest.main()
