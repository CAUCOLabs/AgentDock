from pathlib import Path
import tempfile
import unittest

from git import Repo

from app.config import Settings
from app.github_client import GitHubClient


class GitHubClientTests(unittest.TestCase):
    def test_revert_and_clean_removes_tracked_and_untracked_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo = self._create_repo(repo_path)

            (repo_path / "README.md").write_text("modified\n", encoding="utf-8")
            (repo_path / "scratch.txt").write_text("untracked\n", encoding="utf-8")

            self.assertTrue(repo.is_dirty(untracked_files=True))
            self.assertTrue(repo.untracked_files)

            GitHubClient(Settings()).revert_and_clean(repo)

            self.assertFalse(repo.is_dirty(untracked_files=True))
            self.assertFalse(repo.untracked_files)
            self.assertEqual(
                (repo_path / "README.md").read_text(encoding="utf-8"),
                "initial\n",
            )
            self.assertFalse((repo_path / "scratch.txt").exists())

    def test_remote_info_detects_and_redacts_origin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo = self._create_repo(repo_path)
            repo.create_remote(
                "origin",
                "https://x-access-token:secret-token@github.com/acme/demo.git",
            )

            remote_info = GitHubClient(Settings()).get_remote_info(repo, "main")

            self.assertTrue(remote_info.has_valid_origin)
            self.assertEqual(
                remote_info.remote_url,
                "https://github.com/acme/demo.git",
            )
            self.assertEqual(remote_info.current_branch, repo.active_branch.name)
            self.assertEqual(remote_info.default_branch, "main")
            self.assertFalse(remote_info.push_enabled)
            self.assertFalse(remote_info.pr_creation_enabled)

    def test_remote_info_reports_missing_origin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / "repo"
            repo = self._create_repo(repo_path)

            remote_info = GitHubClient(Settings()).get_remote_info(repo, "main")

            self.assertFalse(remote_info.has_valid_origin)
            self.assertIsNone(remote_info.remote_url)
            self.assertEqual(remote_info.default_branch, "main")

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
