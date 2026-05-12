from hashlib import sha256
import os
from pathlib import Path, PurePosixPath

from git.exc import GitCommandError

from app.config import Settings
from app.github_client import GitHubClient, GitHubClientError
from app.models import RepositoryFileEntry


class RepoBrowserError(Exception):
    """
    Raised when read-only repository browsing fails.
    """


class RepoBrowserService:
    """
    Provides bounded, read-only repository file listing and previews.
    """

    EXCLUDED_DIRS = {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        "__pycache__",
        ".next",
        ".expo",
        "coverage",
    }
    MAX_PREVIEW_BYTES = 64 * 1024
    MAX_LIST_ENTRIES = 5000

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.github_client = GitHubClient(settings)
        self.cache_root = settings.workspace_root / "_repo_browser"
        self.cache_root.mkdir(parents=True, exist_ok=True)

    def list_files(
        self,
        repo_url: str,
        base_branch: str,
    ) -> list[RepositoryFileEntry]:
        repo_path = self._prepare_repo(repo_url, base_branch)
        entries: list[RepositoryFileEntry] = []

        for current_root, dir_names, file_names in os.walk(repo_path):
            root_path = Path(current_root)
            relative_root = root_path.relative_to(repo_path)
            dir_names[:] = [
                name
                for name in sorted(dir_names)
                if name not in self.EXCLUDED_DIRS
            ]

            if relative_root != Path(".") and self._is_excluded(relative_root):
                continue

            for dir_name in dir_names:
                path = root_path / dir_name
                relative_path = path.relative_to(repo_path)
                entries.append(
                    RepositoryFileEntry(
                        path=relative_path.as_posix(),
                        type="directory",
                    )
                )

                if len(entries) >= self.MAX_LIST_ENTRIES:
                    return entries

            for file_name in sorted(file_names):
                path = root_path / file_name
                relative_path = path.relative_to(repo_path)

                if self._is_excluded(relative_path):
                    continue

                entries.append(
                    RepositoryFileEntry(
                        path=relative_path.as_posix(),
                        type="file",
                        size=path.stat().st_size,
                        extension=path.suffix.removeprefix(".") or None,
                    )
                )

                if len(entries) >= self.MAX_LIST_ENTRIES:
                    return entries

        return entries

    def preview_file(
        self,
        repo_url: str,
        base_branch: str,
        file_path: str,
    ) -> tuple[str, int, bool]:
        self.validate_relative_path(file_path)
        repo_path = self._prepare_repo(repo_url, base_branch)
        target_path = (repo_path / file_path).resolve()

        try:
            target_path.relative_to(repo_path.resolve())
        except ValueError as exc:
            raise RepoBrowserError("Invalid file path.") from exc

        relative_path = target_path.relative_to(repo_path)

        if self._is_excluded(relative_path):
            raise RepoBrowserError("File path is excluded from repository browsing.")

        if not target_path.exists() or not target_path.is_file():
            raise RepoBrowserError(f"File not found: {file_path}")

        size = target_path.stat().st_size

        if size > self.MAX_PREVIEW_BYTES:
            raise RepoBrowserError(
                f"File is too large to preview: {size} bytes."
            )

        content_bytes = target_path.read_bytes()

        if b"\x00" in content_bytes:
            raise RepoBrowserError("Binary files cannot be previewed.")

        try:
            return content_bytes.decode("utf-8"), size, False
        except UnicodeDecodeError as exc:
            raise RepoBrowserError("Binary or non-UTF-8 files cannot be previewed.") from exc

    @classmethod
    def validate_relative_path(cls, file_path: str) -> None:
        if "\\" in file_path:
            raise RepoBrowserError("Invalid file path.")

        path = PurePosixPath(file_path)

        if path.is_absolute() or ".." in path.parts:
            raise RepoBrowserError("Invalid file path.")

        if not file_path.strip():
            raise RepoBrowserError("Invalid file path.")

    def task_instruction(
        self,
        instruction: str,
        target_file: str | None,
    ) -> str:
        if not target_file:
            return instruction

        self.validate_relative_path(target_file)

        return (
            f"Selected target file: {target_file}\n\n"
            "Use this selected file as the primary task context. "
            "You may modify other files only if the task requires it.\n\n"
            f"User instruction:\n{instruction}"
        )

    def _prepare_repo(self, repo_url: str, base_branch: str) -> Path:
        repo_path = self._repo_path(repo_url, base_branch)

        if repo_path.exists():
            try:
                repo = self.github_client.open_repository(repo_path)
                repo.git.checkout(base_branch)
                return repo_path
            except (GitCommandError, GitHubClientError) as exc:
                raise RepoBrowserError(
                    f"Failed to open repository browser cache: {repo_path}"
                ) from exc

        try:
            self.github_client.clone_repository(
                repo_url=repo_url,
                destination=repo_path,
                branch=base_branch,
            )
        except GitHubClientError as exc:
            raise RepoBrowserError(str(exc)) from exc

        return repo_path

    def _repo_path(self, repo_url: str, base_branch: str) -> Path:
        digest = sha256(f"{repo_url}:{base_branch}".encode("utf-8")).hexdigest()[:16]
        return self.cache_root / digest / "repo"

    def _is_excluded(self, relative_path: Path) -> bool:
        return any(part in self.EXCLUDED_DIRS for part in relative_path.parts)
