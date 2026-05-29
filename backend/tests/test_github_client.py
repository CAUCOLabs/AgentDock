from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from git import Repo
import httpx

from app.config import Settings
from app.github_client import GitHubClient, GitHubClientError


class StubAsyncClient:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.requests: list[tuple[str, str, dict]] = []

    async def __aenter__(self) -> "StubAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, **kwargs: object) -> httpx.Response:
        self.requests.append(("POST", url, kwargs))
        return self.response

    async def get(self, url: str, **kwargs: object) -> httpx.Response:
        self.requests.append(("GET", url, kwargs))
        return self.response


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

            remote_info = GitHubClient(
                Settings(ALLOW_PUSH=False, ALLOW_PR_CREATION=False)
            ).get_remote_info(repo, "main")

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

    def test_branch_url_generation_uses_owner_repo_and_branch(self) -> None:
        branch_url = GitHubClient(Settings()).build_branch_url(
            "https://github.com/acme/demo.git",
            "agentdock/task_123",
        )

        self.assertEqual(
            branch_url,
            "https://github.com/acme/demo/tree/agentdock/task_123",
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


class GitHubClientWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_workflow_success_expects_204(self) -> None:
        stub = StubAsyncClient(httpx.Response(204))

        with patch("app.github_client.httpx.AsyncClient", return_value=stub):
            result = await GitHubClient(
                Settings(),
                github_token="github-token",
            ).dispatch_workflow(
                "https://github.com/acme/demo",
                "aisoflow-codex.yml",
                "main",
                {"task_id": "task_123"},
            )

        self.assertEqual(result.workflow_id, "aisoflow-codex.yml")
        self.assertEqual(result.ref, "main")
        self.assertEqual(result.status, "dispatched")
        self.assertEqual(result.message, "Workflow dispatch requested.")
        self.assertEqual(len(stub.requests), 1)

        method, url, kwargs = stub.requests[0]
        self.assertEqual(method, "POST")
        self.assertEqual(
            url,
            "https://api.github.com/repos/acme/demo/actions/workflows/"
            "aisoflow-codex.yml/dispatches",
        )
        self.assertEqual(
            kwargs["json"],
            {"ref": "main", "inputs": {"task_id": "task_123"}},
        )
        self.assertEqual(
            kwargs["headers"]["Authorization"],
            "Bearer github-token",
        )

    async def test_dispatch_workflow_404_raises_useful_error(self) -> None:
        stub = StubAsyncClient(httpx.Response(404, text="Not Found"))

        with patch("app.github_client.httpx.AsyncClient", return_value=stub):
            with self.assertRaisesRegex(
                GitHubClientError,
                "Workflow file was not found or GitHub Actions permission is missing",
            ):
                await GitHubClient(
                    Settings(),
                    github_token="github-token",
                ).dispatch_workflow(
                    "https://github.com/acme/demo",
                    "aisoflow-codex.yml",
                    "main",
                )

    async def test_list_workflow_runs_maps_returned_json(self) -> None:
        stub = StubAsyncClient(
            httpx.Response(
                200,
                json={
                    "workflow_runs": [
                        {
                            "id": 42,
                            "name": "AisoFlow Codex",
                            "status": "completed",
                            "conclusion": "success",
                            "html_url": "https://github.com/acme/demo/actions/runs/42",
                            "head_branch": "main",
                            "created_at": "2026-05-29T10:00:00Z",
                            "updated_at": "2026-05-29T10:05:00Z",
                        }
                    ]
                },
            )
        )

        with patch("app.github_client.httpx.AsyncClient", return_value=stub):
            runs = await GitHubClient(
                Settings(),
                github_token="github-token",
            ).list_workflow_runs(
                "https://github.com/acme/demo",
                workflow_id="aisoflow-codex.yml",
                branch="main",
                per_page=5,
            )

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].id, 42)
        self.assertEqual(runs[0].name, "AisoFlow Codex")
        self.assertEqual(runs[0].status, "completed")
        self.assertEqual(runs[0].conclusion, "success")
        self.assertEqual(
            runs[0].html_url,
            "https://github.com/acme/demo/actions/runs/42",
        )
        self.assertEqual(runs[0].head_branch, "main")
        self.assertEqual(runs[0].created_at, "2026-05-29T10:00:00Z")
        self.assertEqual(runs[0].updated_at, "2026-05-29T10:05:00Z")

        method, url, kwargs = stub.requests[0]
        self.assertEqual(method, "GET")
        self.assertEqual(
            url,
            "https://api.github.com/repos/acme/demo/actions/workflows/"
            "aisoflow-codex.yml/runs",
        )
        self.assertEqual(kwargs["params"], {"per_page": 5, "branch": "main"})


if __name__ == "__main__":
    unittest.main()
