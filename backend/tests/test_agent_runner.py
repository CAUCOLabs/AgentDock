from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from git import Repo

from app.agent_runner import (
    AgentRunnerError,
    CodexCliProvider,
    LocalAgentRunner,
    MockAgentRunner,
    get_agent_runner,
)
from app.agent_providers import AgentProviderConnectionStore
from app.agent_providers import CODEX_CHATGPT_OAUTH_AUTH_MODE
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

    def test_codex_cli_runner_captures_process_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir) / "workspaces"
            repo_path = workspace_root / "task_123" / "repo"
            self._create_repo(repo_path)
            settings = self._settings(
                workspace_root,
                codex_enabled=True,
                openai_api_key="openai-key",
            )
            completed = subprocess.CompletedProcess(
                args=["codex"],
                returncode=0,
                stdout="codex out",
                stderr="codex err",
            )

            with (
                patch("app.agent_runner.shutil.which", return_value="/usr/bin/codex"),
                patch("app.agent_runner.subprocess.run", return_value=completed),
            ):
                result = CodexCliProvider(settings).run(
                    repo_path,
                    "Update the selected component.",
                    target_file="src/App.tsx",
                    change_scope="only_selected_file",
                )

            self.assertTrue(result.success)
            self.assertEqual(result.exit_code, 0)
            self.assertIn("codex out", result.stdout)
            self.assertIn("codex err", result.stderr)
            self.assertGreaterEqual(result.duration_seconds or 0, 0)

    def test_codex_cli_runner_times_out(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir) / "workspaces"
            repo_path = workspace_root / "task_123" / "repo"
            self._create_repo(repo_path)
            settings = self._settings(
                workspace_root,
                codex_enabled=True,
                openai_api_key="openai-key",
                codex_timeout=3,
            )

            with (
                patch("app.agent_runner.shutil.which", return_value="/usr/bin/codex"),
                patch(
                    "app.agent_runner.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(
                        cmd=["codex"],
                        timeout=3,
                        output="partial out",
                        stderr="partial err",
                    ),
                ),
            ):
                result = CodexCliProvider(settings).run(repo_path, "Do work.")

            self.assertFalse(result.success)
            self.assertIn("timed out after 3s", result.message)
            self.assertIn("partial out", result.stdout)
            self.assertIn("partial err", result.stderr)
            self.assertIsNone(result.exit_code)

    def test_codex_cli_runner_rejects_paths_outside_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir) / "workspaces"
            outside_repo = Path(temp_dir) / "outside" / "repo"
            self._create_repo(outside_repo)
            settings = self._settings(
                workspace_root,
                codex_enabled=True,
                openai_api_key="openai-key",
            )

            with patch("app.agent_runner.shutil.which", return_value="/usr/bin/codex"):
                with self.assertRaises(AgentRunnerError):
                    CodexCliProvider(settings).run(outside_repo, "unsafe")

    def test_codex_cli_prompt_includes_target_file_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir) / "workspaces"
            repo_path = workspace_root / "task_123" / "repo"
            self._create_repo(repo_path)
            settings = self._settings(
                workspace_root,
                codex_enabled=True,
                openai_api_key="openai-key",
            )
            completed = subprocess.CompletedProcess(
                args=["codex"],
                returncode=0,
                stdout="",
                stderr="",
            )

            with (
                patch("app.agent_runner.shutil.which", return_value="/usr/bin/codex"),
                patch("app.agent_runner.subprocess.run", return_value=completed) as run,
            ):
                CodexCliProvider(settings).run(
                    repo_path,
                    "Fix the render issue.",
                    target_file="src/App.tsx",
                    change_scope="only_selected_file",
                )

            args = run.call_args.args[0]
            prompt = args[-1]
            self.assertNotIn("--ask-for-approval", args)
            self.assertIn("Selected target file: src/App.tsx", prompt)
            self.assertIn("Change scope: only_selected_file", prompt)
            self.assertIn("Modify only the selected target file", prompt)
            self.assertIn("Leave all changes as working tree edits", prompt)
            self.assertIn("Do not commit, push", prompt)

    def test_codex_cli_prompt_allows_whole_project_without_target_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir) / "workspaces"
            repo_path = workspace_root / "task_123" / "repo"
            self._create_repo(repo_path)
            settings = self._settings(
                workspace_root,
                codex_enabled=True,
                openai_api_key="openai-key",
            )
            completed = subprocess.CompletedProcess(
                args=["codex"],
                returncode=0,
                stdout="",
                stderr="",
            )

            with (
                patch("app.agent_runner.shutil.which", return_value="/usr/bin/codex"),
                patch("app.agent_runner.subprocess.run", return_value=completed) as run,
            ):
                CodexCliProvider(settings).run(
                    repo_path,
                    "Create a REST API and models.",
                    change_scope="whole_project",
                )

            prompt = run.call_args.args[0][-1]
            self.assertIn("Selected target file: none", prompt)
            self.assertIn("Change scope: whole_project", prompt)
            self.assertIn("any relevant files across the repository", prompt)

    def test_codex_exec_command_uses_supported_cli_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir) / "workspaces"
            repo_path = workspace_root / "task_123" / "repo"
            self._create_repo(repo_path)
            settings = self._settings(
                workspace_root,
                codex_enabled=True,
                openai_api_key="openai-key",
            )
            completed = subprocess.CompletedProcess(
                args=["codex"],
                returncode=0,
                stdout="",
                stderr="",
            )

            with (
                patch("app.agent_runner.shutil.which", return_value="/usr/bin/codex"),
                patch("app.agent_runner.subprocess.run", return_value=completed) as run,
            ):
                CodexCliProvider(settings).run(repo_path, "Do real work.")

            args = run.call_args.args[0]
            self.assertEqual(args[:4], ["codex", "exec", "--sandbox", "workspace-write"])
            self.assertNotIn("--ask-for-approval", args)
            self.assertNotIn("never", args)

    def test_codex_exec_command_uses_configured_sandbox_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir) / "workspaces"
            settings = self._settings(
                workspace_root,
                codex_sandbox_mode="danger-full-access",
            )

            args = CodexCliProvider(settings)._command_args("Do real work.")

            self.assertEqual(args[:4], ["codex", "exec", "--sandbox", "danger-full-access"])

    def test_codex_exec_command_rejects_invalid_sandbox_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir) / "workspaces"
            settings = self._settings(
                workspace_root,
                codex_sandbox_mode="invalid-mode",
            )

            with self.assertRaisesRegex(
                AgentRunnerError,
                "Invalid CODEX_SANDBOX_MODE",
            ):
                CodexCliProvider(settings)._command_args("Do real work.")

    def test_codex_cli_provider_is_selected_for_local_fallback_auth(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir) / "workspaces",
                codex_enabled=True,
                openai_api_key="openai-key",
            )
            AgentProviderConnectionStore(settings).connect(
                "codex",
                CODEX_CHATGPT_OAUTH_AUTH_MODE,
            )

            with patch("app.agent_runner.shutil.which", return_value="/usr/bin/codex"):
                runner = get_agent_runner(AgentProvider.codex, settings)

            self.assertIsInstance(runner, CodexCliProvider)

    def test_codex_provider_does_not_fallback_without_stored_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir) / "workspaces",
                codex_enabled=True,
                openai_api_key="openai-key",
            )

            with patch("app.agent_runner.shutil.which", return_value="/usr/bin/codex"):
                with self.assertRaises(AgentRunnerError):
                    get_agent_runner(AgentProvider.codex, settings)

    def _settings(
        self,
        workspace_root: Path,
        command: str | None = None,
        timeout: int | None = None,
        codex_enabled: bool = False,
        codex_timeout: int = 900,
        codex_sandbox_mode: str = "workspace-write",
        openai_api_key: str | None = None,
    ) -> Settings:
        return Settings(
            WORKSPACES_DIR=workspace_root,
            LOGS_DIR=workspace_root.parent / "logs",
            LOCAL_RUNNER_COMMAND=command,
            LOCAL_RUNNER_TIMEOUT_SECONDS=timeout,
            CODEX_ENABLED=codex_enabled,
            CODEX_SANDBOX_MODE=codex_sandbox_mode,
            CODEX_TIMEOUT_SECONDS=codex_timeout,
            OPENAI_API_KEY=openai_api_key,
            AGENTDOCK_DB_PATH=workspace_root.parent / "data" / "agentdock.sqlite3",
        )

    def _create_repo(self, repo_path: Path) -> Repo:
        repo_path.mkdir(parents=True, exist_ok=True)
        repo = Repo.init(repo_path)
        with repo.config_writer() as config:
            config.set_value("user", "name", "AisoFlow Tests")
            config.set_value("user", "email", "aisoflow-tests@example.local")
        (repo_path / "README.md").write_text("initial\n", encoding="utf-8")
        repo.git.add(A=True)
        repo.index.commit("initial")
        return repo


if __name__ == "__main__":
    unittest.main()
