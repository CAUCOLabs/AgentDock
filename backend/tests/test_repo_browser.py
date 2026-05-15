from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from git import Repo

from app.config import Settings, get_settings
from app.main import app
from app.repo_browser import RepoBrowserError, RepoBrowserService
from app.task_store import TaskStore


class RepoBrowserTests(unittest.TestCase):
    def test_file_listing_excludes_heavy_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_repo = self._create_repo(Path(temp_dir) / "source")
            settings = self._settings(Path(temp_dir))

            files = RepoBrowserService(settings).list_files(
                str(source_repo.working_tree_dir),
                "main",
            )
            paths = {entry.path for entry in files}

            self.assertIn("src/App.tsx", paths)
            self.assertIn("src", paths)
            self.assertNotIn("node_modules/pkg/index.js", paths)
            self.assertNotIn(".venv/bin/python", paths)
            self.assertNotIn("dist/bundle.js", paths)

    def test_path_traversal_preview_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_repo = self._create_repo(Path(temp_dir) / "source")
            settings = self._settings(Path(temp_dir))

            with self.assertRaises(RepoBrowserError):
                RepoBrowserService(settings).preview_file(
                    str(source_repo.working_tree_dir),
                    "main",
                    "../secret.txt",
                )

    def test_file_preview_limit_returns_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_repo = self._create_repo(
                Path(temp_dir) / "source",
                large_file=True,
            )
            settings = self._settings(Path(temp_dir))

            with self.assertRaisesRegex(RepoBrowserError, "too large"):
                RepoBrowserService(settings).preview_file(
                    str(source_repo.working_tree_dir),
                    "main",
                    "large.txt",
                )

    def test_file_preview_endpoint_returns_text_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_repo = self._create_repo(Path(temp_dir) / "source")
            settings = self._settings(Path(temp_dir))

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                response = client.post(
                    "/repositories/file-preview",
                    json={
                        "repo_url": str(source_repo.working_tree_dir),
                        "base_branch": "main",
                        "path": "src/App.tsx",
                    },
                )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["path"], "src/App.tsx")
            self.assertIn("export function App", payload["content"])

    def test_task_creation_persists_selected_target_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_repo = self._create_repo(Path(temp_dir) / "source")
            settings = self._settings(Path(temp_dir))

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                response = client.post(
                    "/tasks",
                    json={
                        "repo_url": str(source_repo.working_tree_dir),
                        "base_branch": "main",
                        "instruction": "Update the selected component safely.",
                        "agent": "mock",
                        "target_file": "src/App.tsx",
                        "change_scope": "only_selected_file",
                    },
                )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 201)
            task_id = response.json()["task_id"]
            task = TaskStore(settings).get_task(task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.target_file, "src/App.tsx")
            self.assertEqual(task.change_scope.value, "only_selected_file")
            self.assertIn(
                "Target file selected: src/App.tsx",
                [event.message for event in TaskStore(settings).get_logs(task_id)],
            )
            self.assertIn(
                "TODO(AgentDock)",
                TaskStore(settings).get_diff(task_id),
            )
            self.assertIn("src/App.tsx", TaskStore(settings).get_diff(task_id))
            self.assertNotIn("AGENTDOCK_TASK.md", TaskStore(settings).get_diff(task_id))

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                approval_response = client.post(
                    f"/tasks/{task_id}/approval",
                    json={"approve": True},
                )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(approval_response.status_code, 200)
            approved_task = TaskStore(settings).get_task(task_id)
            self.assertIsNotNone(approved_task)
            self.assertEqual(approved_task.status.value, "committed")

    def test_task_creation_persists_change_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_repo = self._create_repo(Path(temp_dir) / "source")
            settings = self._settings(Path(temp_dir))

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                response = client.post(
                    "/tasks",
                    json={
                        "repo_url": str(source_repo.working_tree_dir),
                        "base_branch": "main",
                        "instruction": "Update related files where needed.",
                        "agent": "mock",
                        "target_file": "src/App.tsx",
                        "change_scope": "allow_related_files",
                    },
                )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 201)
            task_id = response.json()["task_id"]
            task = TaskStore(settings).get_task(task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.change_scope.value, "allow_related_files")
            self.assertIn(
                "Scope=allow_related_files",
                TaskStore(settings).get_diff(task_id),
            )

    def _settings(self, temp_path: Path) -> Settings:
        return Settings(
            WORKSPACES_DIR=temp_path / "workspaces",
            LOGS_DIR=temp_path / "logs",
            AGENTDOCK_DB_PATH=temp_path / "data" / "agentdock.sqlite3",
            AGENT_RUNNER="mock",
        )

    def _create_repo(self, repo_path: Path, large_file: bool = False) -> Repo:
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
        (repo_path / "node_modules" / "pkg").mkdir(parents=True)
        (repo_path / "node_modules" / "pkg" / "index.js").write_text(
            "module.exports = {}\n",
            encoding="utf-8",
        )
        (repo_path / ".venv" / "bin").mkdir(parents=True)
        (repo_path / ".venv" / "bin" / "python").write_text(
            "",
            encoding="utf-8",
        )
        (repo_path / "dist").mkdir()
        (repo_path / "dist" / "bundle.js").write_text(
            "",
            encoding="utf-8",
        )

        if large_file:
            (repo_path / "large.txt").write_text(
                "x" * (RepoBrowserService.MAX_PREVIEW_BYTES + 1),
                encoding="utf-8",
            )

        repo.git.add(A=True)
        repo.index.commit("initial")
        return repo


if __name__ == "__main__":
    unittest.main()
