from dataclasses import dataclass
import logging
from pathlib import Path
import subprocess
from time import perf_counter
from urllib.parse import urlparse, urlunparse

import httpx
from git import Repo
from git.exc import GitCommandError

from app.config import Settings
from app.github_auth import redact_token

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class GitPushResult:
    """
    Captured result from an attempted branch push.
    """

    success: bool
    stdout: str
    stderr: str
    exit_code: int | None
    duration_seconds: float
    remote_branch: str
    message: str

    def log_messages(self) -> list[tuple[str, str]]:
        messages: list[tuple[str, str]] = [
            ("info", f"Push duration: {self.duration_seconds:.2f}s."),
        ]

        if self.exit_code is not None:
            level = "info" if self.success else "error"
            messages.append((level, f"Push exit code: {self.exit_code}."))

        if self.stdout.strip():
            messages.append(("info", f"Push stdout:\n{redact_token(self.stdout.strip())}"))

        if self.stderr.strip():
            level = "info" if self.success else "error"
            messages.append((level, f"Push stderr:\n{redact_token(self.stderr.strip())}"))

        return messages


@dataclass(frozen=True)
class GitPullRequestResult:
    """
    Metadata returned after opening a GitHub pull request.
    """

    url: str
    number: int
    state: str


@dataclass(frozen=True)
class GitPullRequestMergeResult:
    """
    Metadata returned after merging a GitHub pull request.
    """

    sha: str
    merged: bool
    message: str


@dataclass(frozen=True)
class GitPullRequestStatusResult:
    """
    Current GitHub pull request state after an API lookup.
    """

    state: str
    merged: bool
    merge_commit_sha: str | None
    merged_by: str | None


class GitHubClient:
    """
    GitHub and Git operations used by AgentDock.

    For the MVP, this class uses GitPython for local repository operations.
    Pull request creation through the GitHub API will be added later.
    """

    def __init__(self, settings: Settings, github_token: str | None = None) -> None:
        self.settings = settings
        self.github_token = github_token or settings.github_token

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
        default_branch: str | None = None,
    ) -> GitPushResult:
        """
        Push a branch to origin.

        Disabled unless ALLOW_PUSH=true.
        """

        if not self.settings.allow_push:
            raise GitHubClientError(
                "Push is disabled. Set ALLOW_PUSH=true to enable it."
            )

        remote_info = self.get_remote_info(repo, default_branch)
        self._validate_push_target(repo, branch, remote_info)

        started_at = perf_counter()

        try:
            completed = subprocess.run(
                ["git", "push", "-u", "origin", branch],
                cwd=repo.working_dir,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            duration_seconds = perf_counter() - started_at
            return GitPushResult(
                success=False,
                stdout="",
                stderr=str(exc),
                exit_code=None,
                duration_seconds=duration_seconds,
                remote_branch=f"origin/{branch}",
                message=f"Push failed to start: {exc}",
            )

        duration_seconds = perf_counter() - started_at
        success = completed.returncode == 0

        return GitPushResult(
            success=success,
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
            duration_seconds=duration_seconds,
            remote_branch=f"origin/{branch}",
            message=(
                f"Push completed for branch {branch}."
                if success
                else f"Push failed for branch {branch}."
            ),
        )

    def build_branch_url(self, repo_url: str, branch: str) -> str:
        """
        Build the canonical GitHub web URL for a repository branch.
        """

        owner, repo_name = self._parse_owner_and_repo(repo_url)
        return f"https://github.com/{owner}/{repo_name}/tree/{branch}"

    async def create_pull_request(
        self,
        repo_url: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> GitPullRequestResult:
        """
        Create a GitHub pull request and return its URL.

        Disabled unless ALLOW_PR_CREATION=true.
        """

        if not self.settings.allow_pr_creation:
            raise GitHubClientError(
                "Pull request creation is disabled. "
                "Set ALLOW_PR_CREATION=true to enable it."
            )

        if not self.github_token:
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
            "Authorization": f"Bearer {self.github_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(api_url, json=payload, headers=headers)

        if response.status_code >= 400:
            if response.status_code == 403 and self.github_token.startswith("ghs_"):
                raise GitHubClientError(
                    "GitHub App needs pull request write permission."
                )

            raise GitHubClientError(
                f"Failed to create pull request: "
                f"{response.status_code} {response.text}"
            )

        data = response.json()
        return GitPullRequestResult(
            url=str(data["html_url"]),
            number=int(data["number"]),
            state=str(data["state"]),
        )

    async def merge_pull_request(
        self,
        repo_url: str,
        pr_number: int,
        commit_title: str,
        commit_message: str,
        merge_method: str = "squash",
    ) -> GitPullRequestMergeResult:
        """
        Merge a GitHub pull request and return merge metadata.
        """

        if merge_method not in {"squash", "merge", "rebase"}:
            raise GitHubClientError(
                "Invalid GITHUB_PR_MERGE_METHOD. Use squash, merge, or rebase."
            )

        if not self.github_token:
            raise GitHubClientError("GITHUB_TOKEN is required to merge a PR.")

        owner, repo_name = self._parse_owner_and_repo(repo_url)
        api_url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls/{pr_number}/merge"
        payload = {
            "commit_title": commit_title,
            "commit_message": commit_message,
            "merge_method": merge_method,
        }
        headers = self._api_headers()

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.put(api_url, json=payload, headers=headers)

        self._log_merge_response(response)

        if response.status_code >= 400:
            raise GitHubClientError(self._merge_error_message(response))

        data = response.json()
        return GitPullRequestMergeResult(
            sha=str(data.get("sha") or ""),
            merged=bool(data.get("merged")),
            message=str(data.get("message") or "Pull request merged."),
        )

    async def get_pull_request_status(
        self,
        repo_url: str,
        pr_number: int,
    ) -> GitPullRequestStatusResult:
        """
        Fetch the current state for a GitHub pull request.
        """

        if not self.github_token:
            raise GitHubClientError("GITHUB_TOKEN is required to inspect a PR.")

        owner, repo_name = self._parse_owner_and_repo(repo_url)
        api_url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls/{pr_number}"

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(api_url, headers=self._api_headers())

        if response.status_code >= 400:
            raise GitHubClientError(
                f"Failed to inspect pull request after merge: "
                f"{response.status_code} {response.text}"
            )

        data = response.json()
        merged_by = data.get("merged_by") or {}
        return GitPullRequestStatusResult(
            state=str(data.get("state") or ""),
            merged=bool(data.get("merged")),
            merge_commit_sha=data.get("merge_commit_sha"),
            merged_by=merged_by.get("login") if isinstance(merged_by, dict) else None,
        )

    async def delete_remote_branch(self, repo_url: str, branch: str) -> None:
        """
        Delete a remote branch ref through the GitHub API.
        """

        if not self.github_token:
            raise GitHubClientError("GITHUB_TOKEN is required to delete a branch.")

        owner, repo_name = self._parse_owner_and_repo(repo_url)
        api_url = f"https://api.github.com/repos/{owner}/{repo_name}/git/refs/heads/{branch}"

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.delete(api_url, headers=self._api_headers())

        if response.status_code >= 400 and response.status_code != 404:
            raise GitHubClientError(
                f"Failed to delete remote branch: {response.status_code} {response.text}"
            )

    def _build_authenticated_url(self, repo_url: str) -> str:
        """
        Add token authentication to HTTPS GitHub URLs when GITHUB_TOKEN is set.

        Public repositories can still be cloned without a token.
        """

        if not self.github_token:
            return repo_url

        parsed = urlparse(repo_url)

        if parsed.scheme != "https":
            return repo_url

        if parsed.hostname != "github.com":
            return repo_url

        netloc = f"x-access-token:{self.github_token}@{parsed.netloc}"

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

    def token_from_origin_remote(self, repo: Repo) -> str | None:
        """
        Return an x-access-token credential embedded in the origin remote URL.

        GitHub App task workspaces are cloned with an installation token in
        the origin remote so subsequent Git operations can push without a
        classic personal access token.
        """

        remote_url = self._origin_url(repo)

        if not remote_url:
            return None

        parsed = urlparse(remote_url)

        if parsed.hostname != "github.com":
            return None

        if parsed.username != "x-access-token" or not parsed.password:
            return None

        return parsed.password

    def _api_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.github_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _merge_error_message(self, response: httpx.Response) -> str:
        text = response.text
        lowered = text.lower()

        if response.status_code == 409:
            return "Merge conflict: resolve conflicts on GitHub before merging."

        if "already merged" in lowered:
            return "Pull request is already merged."

        if "closed" in lowered:
            return "Pull request is closed and cannot be merged."

        if "review" in lowered:
            return "Review required before merge."

        if (
            "protected" in lowered
            or "branch protection" in lowered
            or "required status" in lowered
            or "status check" in lowered
        ):
            return "Branch protection prevents merge."

        if response.status_code in {401, 403, 404}:
            if self.github_token and self.github_token.startswith("ghs_"):
                return (
                    "GitHub App needs contents write and pull request write "
                    "permission to merge."
                )
            return "Permission missing: GitHub token cannot merge this pull request."

        if response.status_code == 405:
            return "Pull request is not mergeable yet."

        return f"Failed to merge pull request: {response.status_code} {text}"

    def _log_merge_response(self, response: httpx.Response) -> None:
        body = redact_token(response.text)
        if len(body) > 1000:
            body = f"{body[:1000]}..."

        logger.info(
            "GitHub merge API response: status=%s body=%s",
            response.status_code,
            body,
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

    def _validate_push_target(
        self,
        repo: Repo,
        branch: str,
        remote_info: GitRemoteInfo,
    ) -> None:
        if not remote_info.has_valid_origin:
            raise GitHubClientError("Cannot push because origin remote is missing.")

        if not branch:
            raise GitHubClientError("Cannot push because branch is missing.")

        protected_branches = {
            "main",
            "master",
            self.settings.github_default_branch,
            remote_info.default_branch,
        }

        if branch in protected_branches:
            raise GitHubClientError(f"Refusing to push protected branch: {branch}")

        if not branch.startswith("agentdock/"):
            raise GitHubClientError(
                f"Refusing to push non-AgentDock task branch: {branch}"
            )

        if self._current_branch(repo) != branch:
            raise GitHubClientError(
                f"Refusing to push branch {branch}; current branch is "
                f"{self._current_branch(repo) or 'detached'}."
            )

        try:
            repo.git.check_ref_format("--branch", branch)
        except GitCommandError as exc:
            raise GitHubClientError(f"Invalid branch name for push: {branch}") from exc

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
