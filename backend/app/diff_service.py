from pathlib import Path

from git import Repo
from git.exc import InvalidGitRepositoryError, NoSuchPathError

from app.models import ChangedFile


class DiffServiceError(Exception):
    """
    Raised when a diff operation fails.
    """


class DiffService:
    """
    Provides Git diff utilities for AgentDock task workspaces.

    The service expects a valid Git repository path.
    """

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path
        self.repo = self._load_repo(repo_path)

    def get_diff(self) -> str:
        """
        Return the full Git diff for tracked and untracked changes.
        """

        tracked_diff = self.repo.git.diff()
        untracked_diff = self._get_untracked_diff()

        if tracked_diff and untracked_diff:
            return f"{tracked_diff}\n{untracked_diff}"

        return tracked_diff or untracked_diff

    def get_changed_files(self) -> list[ChangedFile]:
        """
        Return changed files with basic status and line stats.
        """

        changed_files: list[ChangedFile] = []

        # Tracked modified/deleted/renamed files
        for item in self.repo.index.diff(None):
            file_path = item.a_path or item.b_path
            if not file_path:
                continue

            status = self._change_type_to_status(item.change_type)
            additions, deletions = self._get_file_stats(file_path)

            changed_files.append(
                ChangedFile(
                    path=file_path,
                    status=status,
                    additions=additions,
                    deletions=deletions,
                )
            )

        # Staged files
        for item in self.repo.index.diff("HEAD"):
            file_path = item.a_path or item.b_path
            if not file_path:
                continue

            if self._already_listed(changed_files, file_path):
                continue

            status = self._change_type_to_status(item.change_type)
            additions, deletions = self._get_file_stats(file_path)

            changed_files.append(
                ChangedFile(
                    path=file_path,
                    status=status,
                    additions=additions,
                    deletions=deletions,
                )
            )

        # Untracked files
        for file_path in self.repo.untracked_files:
            additions = self._count_file_lines(self.repo_path / file_path)

            changed_files.append(
                ChangedFile(
                    path=file_path,
                    status="added",
                    additions=additions,
                    deletions=0,
                )
            )

        return changed_files

    def has_changes(self) -> bool:
        """
        Return True if the repository has tracked or untracked changes.
        """

        return self.repo.is_dirty(untracked_files=True) or bool(self.repo.untracked_files)

    def _load_repo(self, repo_path: Path) -> Repo:
        """
        Load a Git repository from disk.
        """

        try:
            return Repo(repo_path)
        except (InvalidGitRepositoryError, NoSuchPathError) as exc:
            raise DiffServiceError(f"Invalid Git repository: {repo_path}") from exc

    def _get_untracked_diff(self) -> str:
        """
        Build a simple diff-like output for untracked files.

        Git does not include untracked files in regular `git diff`,
        so we generate a readable representation for review.
        """

        parts: list[str] = []

        for file_path in self.repo.untracked_files:
            absolute_path = self.repo_path / file_path

            if not absolute_path.is_file():
                continue

            try:
                content = absolute_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                parts.append(
                    f"diff --git a/{file_path} b/{file_path}\n"
                    f"new file mode 100644\n"
                    f"--- /dev/null\n"
                    f"+++ b/{file_path}\n"
                    f"@@ binary or non-utf8 file not displayed @@\n"
                )
                continue

            lines = content.splitlines()
            added_lines = "\n".join(f"+{line}" for line in lines)

            parts.append(
                f"diff --git a/{file_path} b/{file_path}\n"
                f"new file mode 100644\n"
                f"--- /dev/null\n"
                f"+++ b/{file_path}\n"
                f"@@ -0,0 +1,{len(lines)} @@\n"
                f"{added_lines}\n"
            )

        return "\n".join(parts)

    def _get_file_stats(self, file_path: str) -> tuple[int, int]:
        """
        Return additions and deletions for a file using git numstat.
        """

        try:
            output = self.repo.git.diff("--numstat", "--", file_path)
        except Exception:
            return 0, 0

        if not output.strip():
            try:
                output = self.repo.git.diff("--cached", "--numstat", "--", file_path)
            except Exception:
                return 0, 0

        if not output.strip():
            return 0, 0

        first_line = output.splitlines()[0]
        parts = first_line.split("\t")

        if len(parts) < 2:
            return 0, 0

        additions_raw, deletions_raw = parts[0], parts[1]

        additions = int(additions_raw) if additions_raw.isdigit() else 0
        deletions = int(deletions_raw) if deletions_raw.isdigit() else 0

        return additions, deletions

    def _count_file_lines(self, path: Path) -> int:
        """
        Count lines in a text file.
        """

        try:
            return len(path.read_text(encoding="utf-8").splitlines())
        except UnicodeDecodeError:
            return 0
        except FileNotFoundError:
            return 0

    def _change_type_to_status(self, change_type: str) -> str:
        """
        Convert GitPython change type to a readable status.
        """

        mapping = {
            "A": "added",
            "D": "deleted",
            "M": "modified",
            "R": "renamed",
            "T": "type_changed",
        }

        return mapping.get(change_type, "modified")

    def _already_listed(self, files: list[ChangedFile], file_path: str) -> bool:
        """
        Avoid duplicated changed file entries.
        """

        return any(file.path == file_path for file in files)