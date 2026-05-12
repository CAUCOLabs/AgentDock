from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx
from git import Repo
from git.exc import GitCommandError

from app.config import Settings


class GitHubClientError(Exception):
    """
    Raised when a GitHub or Git operation fails.
    """


@dataclass(frozen=True)
class GitRemoteInfo:
    """
    Read-only remote metadata for workspace readiness checks.
    """

    remote_url: str | None
    has_valid_origin: bool
    current_branch: str | None
    default_branch: str
    push_enabled: bool
    pr_creation_enabled: bool


class GitHubClient:
    """
    GitHub and Git operations used by AgentDock.

    For the MVP, this class uses GitPython for local repository operations.
    Pull request creation through the GitHub API will be added later.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def clone_repository(
        self,
        repo_url: str,
        destination: Path,
        branch: str = "main",
    ) -> Repo:
        """
        Clone a GitHub repository into the given destination path.
        """

        if destination.exists() and any(destination.iterdir()):
            raise GitHubClientError(f"Destination is not empty: {destination}")

        destination.parent.mkdir(parents=True, exist_ok=True)

        clone_url = self._build_authenticated_url(repo_url)

        try:
            repo = Repo.clone_from(
                clone_url,
                destination,
                branch=branch,
                depth=1,
            )
        except GitCommandError as exc:
            raise GitHubClientError(
                f"Failed to clone repository '{repo_url}' branch '{branch}'."
            ) from exc

        self.configure_git_user(repo)
        return repo

    def open_repository(self, repo_path: Path) -> Repo:
        """
        Open an existing local Git repository.
        """

        try:
            return Repo(repo_path)
        except Exception as exc:
            raise GitHubClientError(f"Failed to open repository: {repo_path}") from exc

    def create_working_branch(
        self,
        repo: Repo,
        working_branch: str,
    ) -> None:
        """
        Create and checkout a dedicated working branch.
        """

        try:
            existing_branches = [head.name for head in repo.heads]

            if working_branch in existing_branches:
                repo.git.checkout(working_branch)
                return

            repo.git.checkout("-b", working_branch)
        except GitCommandError as exc:
            raise GitHubClientError(
                f"Failed to create working branch: {working_branch}"
            ) from exc

    def configure_git_user(self, repo: Repo) -> None:
        """
        Configure a local Git identity for AgentDock commits.

        This only affects the cloned workspace repository.
        """

        try:
            with repo.config_writer() as config:
                config.set_value("user", "name", "AgentDock")
                config.set_value("user", "email", "agentdock@example.local")
        except Exception as exc:
            raise GitHubClientError("Failed to configure Git user.") from exc

    def commit_all(
        self,
        repo: Repo,
        message: str,
    ) -> str:
        """
        Stage and commit all current repository changes.

        Returns the created commit SHA.
        """

        if not repo.is_dirty(untracked_files=True) and not repo.untracked_files:
            raise GitHubClientError("No changes to commit.")

        try:
            repo.git.add(A=True)
            commit = repo.index.commit(message)
            return commit.hexsha
        except GitCommandError as exc:
            raise GitHubClientError("Failed to create commit.") from exc

    def revert_and_clean(self, repo: Repo) -> None:
        """
        Reset tracked changes and remove untracked files from a task repo.
        """

        try:
            repo.git.reset("--hard", "HEAD")
            repo.git.clean("-fd")
        except GitCommandError as exc:
            raise GitHubClientError("Failed to revert and clean workspace.") from exc

    def get_remote_info(
        self,
        repo: Repo,
        default_branch: str | None = None,
    ) -> GitRemoteInfo:
        """
        Return safe origin remote metadata for API readiness reporting.
        """

        origin_url = self._origin_url(repo)

        return GitRemoteInfo(
            remote_url=self._redact_remote_url(origin_url),
            has_valid_origin=bool(origin_url),
            current_branch=self._current_branch(repo),
            default_branch=(
                self._origin_default_branch(repo)
                or default_branch
                or self.settings.github_default_branch
            ),
            push_enabled=self.settings.allow_push,
            pr_creation_enabled=self.settings.allow_pr_creation,
        )

    def push_branch(
        self,
        repo: Repo,
        branch: str,
    ) -> None:
        """
        Push a branch to origin.

        Disabled unless ALLOW_PUSH=true.
        """

        if not self.settings.allow_push:
            raise GitHubClientError(
                "Push is disabled. Set ALLOW_PUSH=true to enable it."
            )

        try:
            repo.git.push("--set-upstream", "origin", branch)
        except GitCommandError as exc:
            raise GitHubClientError(f"Failed to push branch: {branch}") from exc

    async def create_pull_request(
        self,
        repo_url: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> str:
        """
        Create a GitHub pull request and return its URL.

        Disabled unless ALLOW_PR_CREATION=true.
        """

        if not self.settings.allow_pr_creation:
            raise GitHubClientError(
                "Pull request creation is disabled. "
                "Set ALLOW_PR_CREATION=true to enable it."
            )

        if not self.settings.github_token:
            raise GitHubClientError("GITHUB_TOKEN is required to create a PR.")

        owner, repo_name = self._parse_owner_and_repo(repo_url)

        api_url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls"

        payload = {
            "title": title,
            "head": head_branch,
            "base": base_branch,
            "body": body,
        }

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.settings.github_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(api_url, json=payload, headers=headers)

        if response.status_code >= 400:
            raise GitHubClientError(
                f"Failed to create pull request: "
                f"{response.status_code} {response.text}"
            )

        data = response.json()
        return str(data["html_url"])

    def _build_authenticated_url(self, repo_url: str) -> str:
        """
        Add token authentication to HTTPS GitHub URLs when GITHUB_TOKEN is set.

        Public repositories can still be cloned without a token.
        """

        if not self.settings.github_token:
            return repo_url

        parsed = urlparse(repo_url)

        if parsed.scheme != "https":
            return repo_url

        if parsed.hostname != "github.com":
            return repo_url

        netloc = f"x-access-token:{self.settings.github_token}@{parsed.netloc}"

        return urlunparse(
            (
                parsed.scheme,
                netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )

    def _origin_url(self, repo: Repo) -> str | None:
        for remote in repo.remotes:
            if remote.name != "origin":
                continue

            urls = list(remote.urls)

            if not urls:
                return None

            return urls[0]

        return None

    def _current_branch(self, repo: Repo) -> str | None:
        try:
            return repo.active_branch.name
        except TypeError:
            return None

    def _origin_default_branch(self, repo: Repo) -> str | None:
        try:
            ref_name = repo.git.symbolic_ref("--short", "refs/remotes/origin/HEAD")
        except GitCommandError:
            return None

        prefix = "origin/"

        if ref_name.startswith(prefix):
            return ref_name.removeprefix(prefix)

        return ref_name or None

    def _redact_remote_url(self, remote_url: str | None) -> str | None:
        if not remote_url:
            return None

        parsed = urlparse(remote_url)

        if not parsed.username and not parsed.password:
            return remote_url

        hostname = parsed.hostname or ""
        netloc = hostname

        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"

        return urlunparse(
            (
                parsed.scheme,
                netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )

    def _parse_owner_and_repo(self, repo_url: str) -> tuple[str, str]:
        """
        Parse owner and repo name from a GitHub HTTPS URL.
        """

        parsed = urlparse(repo_url)

        if parsed.hostname != "github.com":
            raise GitHubClientError(f"Unsupported GitHub URL: {repo_url}")

        parts = parsed.path.strip("/").split("/")

        if len(parts) < 2:
            raise GitHubClientError(f"Invalid GitHub repository URL: {repo_url}")

        owner = parts[0]
        repo_name = parts[1].removesuffix(".git")

        return owner, repo_name
