from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from git import Repo

from app.config import Settings, get_settings
from app.github_auth import GitHubAuthService, GitHubAuthStore, redact_token
from app.github_client import GitPushResult
from app.main import app
from app.agent_providers import AgentProviderConnectionStore, provider_metadata


class GitHubAuthTests(unittest.TestCase):
    def test_github_app_start_route_exists_and_returns_install_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                github_app_install_url="https://github.com/apps/agentdock/installations/new",
            )
            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                response = client.get("/auth/github-app/start")
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertIn(
                "https://github.com/apps/agentdock/installations/new",
                payload["auth_url"],
            )
            self.assertIn("state=", payload["auth_url"])
            self.assertTrue(payload["session_id"])

    def test_github_app_missing_config_returns_400_not_404(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                github_app_install_url="",
                github_app_slug="",
            )
            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                response = client.get("/auth/github-app/start")
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["detail"], "GitHub App is not configured")

    def test_github_app_installation_persists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))
            store = GitHubAuthStore(settings)
            session_id, _state = store.create_pending_session()

            installation = store.save_app_installation(
                session_id=session_id,
                installation_id=123,
                account_login="acme",
                account_type="Organization",
                selected_repositories=["acme/demo"],
                permissions={"contents": "read", "pull_requests": "write"},
            )

            restarted_session = GitHubAuthStore(settings).get_app_session(session_id)

            self.assertEqual(installation.installation_id, 123)
            self.assertIsNotNone(restarted_session)
            self.assertEqual(restarted_session.installation.account_login, "acme")
            self.assertEqual(
                restarted_session.installation.selected_repositories,
                ["acme/demo"],
            )
            self.assertEqual(
                restarted_session.installation.permissions["contents"],
                "read",
            )

    def test_repo_listing_uses_installation_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))
            store = GitHubAuthStore(settings)
            session_id, _state = store.create_pending_session()
            store.save_app_installation(
                session_id=session_id,
                installation_id=123,
                account_login="acme",
                account_type="Organization",
                selected_repositories=["acme/demo"],
                permissions={"contents": "read"},
            )
            service = StubGitHubAppService(settings)

            repos = service.run_list_app_repositories(session_id)

            self.assertEqual(service.token_installation_id, 123)
            self.assertEqual(service.repository_token, "installation-token")
            self.assertEqual(repos[0].full_name, "acme/demo")
            self.assertTrue(repos[0].can_read)

    def test_github_app_flow_does_not_connect_ai_providers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))
            store = GitHubAuthStore(settings)
            session_id, _state = store.create_pending_session()
            store.save_app_installation(
                session_id=session_id,
                installation_id=123,
                account_login="acme",
                account_type="Organization",
                selected_repositories=["acme/demo"],
                permissions={"contents": "read"},
            )
            service = StubGitHubAppService(settings)

            service.run_list_app_repositories(session_id)

            self.assertEqual(
                AgentProviderConnectionStore(settings).connected_auth_modes(),
                {},
            )
            providers = {provider.id: provider for provider in provider_metadata(settings)}
            self.assertTrue(
                all(provider.connection.value == "disconnected" for provider in providers.values())
            )

    def test_auth_state_persists_account_without_exposing_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))
            store = GitHubAuthStore(settings)
            session_id, _state = store.create_pending_session()

            account = store.save_authenticated_session(
                session_id=session_id,
                account_data={
                    "id": 42,
                    "login": "octocat",
                    "name": "The Octocat",
                    "avatar_url": "https://avatars.githubusercontent.com/u/42",
                    "html_url": "https://github.com/octocat",
                },
                access_token="gho_secret-token",
                scopes="repo,read:user",
            )

            restarted_session = GitHubAuthStore(settings).get_session(session_id)

            self.assertEqual(account.login, "octocat")
            self.assertIsNotNone(restarted_session)
            self.assertEqual(restarted_session.account.login, "octocat")
            self.assertEqual(restarted_session.access_token, "gho_secret-token")
            self.assertFalse(hasattr(restarted_session.account, "access_token"))

    def test_github_oauth_flow_does_not_connect_ai_providers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))
            store = GitHubAuthStore(settings)
            session_id, _state = store.create_pending_session()

            store.save_authenticated_session(
                session_id=session_id,
                account_data={
                    "id": 42,
                    "login": "octocat",
                    "name": "The Octocat",
                    "avatar_url": "https://avatars.githubusercontent.com/u/42",
                    "html_url": "https://github.com/octocat",
                },
                access_token="gho_secret-token",
                scopes="repo,read:user",
            )

            self.assertEqual(
                AgentProviderConnectionStore(settings).connected_auth_modes(),
                {},
            )
            providers = {provider.id: provider for provider in provider_metadata(settings)}
            self.assertTrue(
                all(provider.connection.value == "disconnected" for provider in providers.values())
            )

    def test_token_redaction_handles_known_prefixes_and_push_logs(self) -> None:
        self.assertNotIn(
            "ghp_secret",
            redact_token("remote: token ghp_secret failed"),
        )

        result = GitPushResult(
            success=False,
            stdout="",
            stderr="fatal: https://x-access-token:ghp_secret@github.com/acme/repo.git",
            exit_code=1,
            duration_seconds=0.1,
            remote_branch="origin/agentdock/task",
            message="failed",
        )

        messages = "\n".join(message for _level, message in result.log_messages())

        self.assertNotIn("ghp_secret", messages)
        self.assertIn("[REDACTED]", messages)

    def test_repo_list_mapping_sets_permissions_and_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = GitHubAuthService(self._settings(Path(temp_dir)))

            repo = service._map_repository(
                {
                    "id": 1,
                    "name": "demo",
                    "full_name": "acme/demo",
                    "html_url": "https://github.com/acme/demo",
                    "clone_url": "https://github.com/acme/demo.git",
                    "default_branch": "trunk",
                    "private": True,
                    "visibility": "private",
                    "permissions": {
                        "pull": True,
                        "push": False,
                        "admin": False,
                    },
                }
            )

            self.assertTrue(repo.can_read)
            self.assertFalse(repo.can_push)
            self.assertEqual(repo.default_branch, "trunk")
            self.assertTrue(repo.private)
            self.assertEqual(repo.visibility, "private")

    def test_unauthenticated_access_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))
            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                me_response = client.get("/auth/github/me")
                repos_response = client.get("/auth/github/repos")
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(me_response.status_code, 401)
            self.assertEqual(repos_response.status_code, 401)

    def test_manual_repo_url_fallback_still_lists_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_repo = self._create_repo(Path(temp_dir) / "source")
            settings = self._settings(Path(temp_dir))
            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                response = client.post(
                    "/repositories/files",
                    json={
                        "repo_url": str(source_repo.working_tree_dir),
                        "base_branch": "main",
                    },
                )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            paths = {entry["path"] for entry in response.json()["files"]}
            self.assertIn("README.md", paths)

    def _settings(
        self,
        temp_path: Path,
        github_app_install_url: str | None = None,
        github_app_slug: str | None = None,
    ) -> Settings:
        return Settings(
            WORKSPACES_DIR=temp_path / "workspaces",
            LOGS_DIR=temp_path / "logs",
            AGENTDOCK_DB_PATH=temp_path / "data" / "agentdock.sqlite3",
            GITHUB_OAUTH_CLIENT_ID="client-id",
            GITHUB_OAUTH_CLIENT_SECRET="client-secret",
            GITHUB_APP_INSTALL_URL=github_app_install_url,
            GITHUB_APP_SLUG=github_app_slug,
        )

    def _create_repo(self, repo_path: Path) -> Repo:
        repo_path.mkdir(parents=True, exist_ok=True)
        repo = Repo.init(repo_path, initial_branch="main")
        with repo.config_writer() as config:
            config.set_value("user", "name", "AgentDock Tests")
            config.set_value("user", "email", "agentdock-tests@example.local")
        (repo_path / "README.md").write_text("hello\n", encoding="utf-8")
        repo.git.add(A=True)
        repo.index.commit("initial")
        return repo


class StubGitHubAppService(GitHubAuthService):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.token_installation_id: int | None = None
        self.repository_token: str | None = None

    async def create_installation_token(self, installation_id: int) -> str:
        self.token_installation_id = installation_id
        return "installation-token"

    async def _list_installation_repository_data(
        self,
        installation_token: str,
    ) -> list[dict[str, object]]:
        self.repository_token = installation_token
        return [
            {
                "id": 1,
                "name": "demo",
                "full_name": "acme/demo",
                "html_url": "https://github.com/acme/demo",
                "clone_url": "https://github.com/acme/demo.git",
                "default_branch": "main",
                "private": False,
                "visibility": "public",
                "permissions": {"pull": True, "push": False},
            }
        ]

    def run_list_app_repositories(self, session_id: str):
        import asyncio

        return asyncio.run(self.list_app_repositories(session_id))


if __name__ == "__main__":
    unittest.main()
