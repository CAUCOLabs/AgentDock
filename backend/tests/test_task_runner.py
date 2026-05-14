from pathlib import Path
import sys
import tempfile
import unittest

from git import Repo

from app.config import Settings
from app.models import AgentProvider
from app.task_runner import TaskRunner, TaskRunnerError


class TaskRunnerTests(unittest.TestCase):
    def test_mock_runner_still_produces_review_diff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_repo = self._create_source_repo(Path(temp_dir) / "source")
            settings = self._settings(Path(temp_dir) / "mock-run")

            result = TaskRunner(settings).run_task(
                task_id="task_mock",
                repo_url=str(source_repo.working_tree_dir),
                base_branch="main",
                working_branch="agentdock/task_mock",
                instruction="Create the default mock task note.",
                agent=AgentProvider.mock,
            )

            self.assertIn("AGENTDOCK_TASK.md", result.diff)
            self.assertEqual(result.changed_files[0].path, "AGENTDOCK_TASK.md")

    def test_development_runner_modifies_selected_target_file_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_repo = self._create_source_repo(Path(temp_dir) / "source")
            settings = self._settings(Path(temp_dir) / "target-run")

            result = TaskRunner(settings).run_task(
                task_id="task_target",
                repo_url=str(source_repo.working_tree_dir),
                base_branch="main",
                working_branch="agentdock/task_target",
                instruction="Append a safe marker to the selected file.",
                agent=AgentProvider.codex,
                target_file="src/App.tsx",
                change_scope="only_selected_file",
            )

            self.assertIn("src/App.tsx", result.diff)
            self.assertIn("TODO(AgentDock)", result.diff)
            self.assertEqual(
                [changed_file.path for changed_file in result.changed_files],
                ["src/App.tsx"],
            )
            self.assertNotIn("AGENTDOCK_TASK.md", result.diff)

    def test_development_runner_uses_markdown_marker_for_markdown_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_repo = self._create_source_repo(Path(temp_dir) / "source")
            settings = self._settings(Path(temp_dir) / "markdown-run")

            result = TaskRunner(settings).run_task(
                task_id="task_markdown",
                repo_url=str(source_repo.working_tree_dir),
                base_branch="main",
                working_branch="agentdock/task_markdown",
                instruction="Add review note.",
                agent=AgentProvider.codex,
                target_file="README.md",
                change_scope="only_selected_file",
            )

            self.assertIn("README.md", result.diff)
            self.assertIn("<!-- AgentDock development note:", result.diff)
            self.assertEqual(
                [changed_file.path for changed_file in result.changed_files],
                ["README.md"],
            )

    def test_local_runner_modifies_file_and_produces_real_diff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_repo = self._create_source_repo(Path(temp_dir) / "source")
            command = (
                f"{sys.executable} -c "
                "\"from pathlib import Path; "
                "Path('local.txt').write_text('local change\\\\n'); "
                "print('changed local.txt')\""
            )
            settings = self._settings(
                Path(temp_dir) / "local-run",
                agent_runner="local",
                command=command,
            )

            result = TaskRunner(settings).run_task(
                task_id="task_local",
                repo_url=str(source_repo.working_tree_dir),
                base_branch="main",
                working_branch="agentdock/task_local",
                instruction="Run local command.",
                agent=AgentProvider.mock,
            )

            self.assertIn("local.txt", result.diff)
            self.assertIn("+local change", result.diff)
            self.assertTrue(
                any(
                    "Runner exit code: 0." in message
                    for _, message in result.log_messages
                )
            )

    def test_failed_local_runner_marks_task_run_as_failed_with_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_repo = self._create_source_repo(Path(temp_dir) / "source")
            command = (
                f"{sys.executable} -c "
                "\"import sys; print('boom', file=sys.stderr); sys.exit(3)\""
            )
            settings = self._settings(
                Path(temp_dir) / "failed-run",
                agent_runner="local",
                command=command,
            )

            with self.assertRaises(TaskRunnerError) as context:
                TaskRunner(settings).run_task(
                    task_id="task_fail",
                    repo_url=str(source_repo.working_tree_dir),
                    base_branch="main",
                    working_branch="agentdock/task_fail",
                    instruction="Fail local command.",
                    agent=AgentProvider.mock,
                )

            self.assertIn("Local runner failed.", str(context.exception))
            self.assertTrue(
                any(
                    "Runner exit code: 3." in message
                    for _, message in context.exception.log_messages
                )
            )
            self.assertTrue(
                any("boom" in message for _, message in context.exception.log_messages)
            )

    def _settings(
        self,
        workspace_root: Path,
        agent_runner: str = "mock",
        command: str | None = None,
    ) -> Settings:
        return Settings(
            WORKSPACES_DIR=workspace_root / "workspaces",
            LOGS_DIR=workspace_root / "logs",
            AGENT_RUNNER=agent_runner,
            LOCAL_RUNNER_COMMAND=command,
            LOCAL_RUNNER_TIMEOUT_SECONDS=5,
        )

    def _create_source_repo(self, repo_path: Path) -> Repo:
        repo_path.mkdir(parents=True, exist_ok=True)
        repo = Repo.init(repo_path, initial_branch="main")
        with repo.config_writer() as config:
            config.set_value("user", "name", "AgentDock Tests")
            config.set_value("user", "email", "agentdock-tests@example.local")
        (repo_path / "README.md").write_text("initial\n", encoding="utf-8")
        src_dir = repo_path / "src"
        src_dir.mkdir()
        (src_dir / "App.tsx").write_text(
            "export function App() {\n  return null\n}\n",
            encoding="utf-8",
        )
        repo.git.add(A=True)
        repo.index.commit("initial")
        return repo


if __name__ == "__main__":
    unittest.main()
