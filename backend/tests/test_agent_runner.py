from pathlib import Path
import sys
import tempfile
import unittest

from git import Repo

from app.agent_runner import AgentRunnerError, LocalAgentRunner, MockAgentRunner, get_agent_runner
from app.config import Settings
from app.models import AgentProvider


class LocalAgentRunnerTests(unittest.TestCase):
    def test_local_runner_executes_inside_workspace_and_captures_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir) / "workspaces"
            repo_path = workspace_root / "task_123" / "repo"
            self._create_repo(repo_path)

            command = (
                f"{sys.executable} -c "
                "\"from pathlib import Path; "
                "Path('local.txt').write_text('changed\\\\n'); "
                "print('done')\""
            )
            runner = LocalAgentRunner(
                self._settings(workspace_root, command=command, timeout=5)
            )

            result = runner.run(repo_path, "write a file")

            self.assertTrue(result.success)
            self.assertEqual(result.exit_code, 0)
            self.assertIn("done", result.stdout)
            self.assertGreaterEqual(result.duration_seconds or 0, 0)
            self.assertEqual((repo_path / "local.txt").read_text(), "changed\n")

    def test_local_runner_failed_command_preserves_output_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir) / "workspaces"
            repo_path = workspace_root / "task_123" / "repo"
            self._create_repo(repo_path)

            command = (
                f"{sys.executable} -c "
                "\"import sys; print('out'); print('err', file=sys.stderr); "
                "sys.exit(7)\""
            )
            runner = LocalAgentRunner(
                self._settings(workspace_root, command=command, timeout=5)
            )

            result = runner.run(repo_path, "fail")

            self.assertFalse(result.success)
            self.assertEqual(result.exit_code, 7)
            self.assertIn("out", result.stdout)
            self.assertIn("err", result.stderr)
            self.assertIn(("error", "Runner exit code: 7."), result.log_messages())

    def test_local_runner_rejects_paths_outside_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir) / "workspaces"
            outside_repo = Path(temp_dir) / "outside" / "repo"
            self._create_repo(outside_repo)

            runner = LocalAgentRunner(
                self._settings(workspace_root, command=f"{sys.executable} --version")
            )

            with self.assertRaises(AgentRunnerError):
                runner.run(outside_repo, "unsafe")

    def test_mock_runner_is_default_when_local_runner_is_not_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir) / "workspaces")

            runner = get_agent_runner(AgentProvider.mock, settings)

            self.assertIsInstance(runner, MockAgentRunner)

    def _settings(
        self,
        workspace_root: Path,
        command: str | None = None,
        timeout: int | None = None,
    ) -> Settings:
        return Settings(
            WORKSPACES_DIR=workspace_root,
            LOGS_DIR=workspace_root.parent / "logs",
            LOCAL_RUNNER_COMMAND=command,
            LOCAL_RUNNER_TIMEOUT_SECONDS=timeout,
        )

    def _create_repo(self, repo_path: Path) -> Repo:
        repo_path.mkdir(parents=True, exist_ok=True)
        repo = Repo.init(repo_path)
        with repo.config_writer() as config:
            config.set_value("user", "name", "AgentDock Tests")
            config.set_value("user", "email", "agentdock-tests@example.local")
        (repo_path / "README.md").write_text("initial\n", encoding="utf-8")
        repo.git.add(A=True)
        repo.index.commit("initial")
        return repo


if __name__ == "__main__":
    unittest.main()
