import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import Settings
from app.models import GitHubAccountResponse, GitHubRepositorySummary


class GitHubAuthError(Exception):
    """
    Raised when GitHub authentication or authenticated API access fails.
    """


@dataclass(frozen=True)
class StoredGitHubSession:
    session_id: str
    access_token: str
    account: GitHubAccountResponse


def redact_token(value: str, token: str | None = None) -> str:
    """
    Remove known token-like values from logs and user-facing errors.
    """

    redacted = value

    if token:
        redacted = redacted.replace(token, "[REDACTED]")

    for marker in ("ghp_", "gho_", "ghu_", "ghs_", "github_pat_"):
        index = redacted.find(marker)
        while index != -1:
            end = index
            while end < len(redacted) and not redacted[end].isspace():
                end += 1
            redacted = f"{redacted[:index]}[REDACTED]{redacted[end:]}"
            index = redacted.find(marker)

    return redacted


class GitHubAuthStore:
    """
    SQLite persistence for GitHub OAuth state, sessions, and account metadata.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db_path = settings.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    def create_pending_session(self) -> tuple[str, str]:
        session_id = secrets.token_urlsafe(32)
        state = secrets.token_urlsafe(32)
        now = self._datetime_to_text(datetime.now(timezone.utc))

        with self._connect() as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO github_oauth_states (
                        state,
                        session_id,
                        created_at
                    )
                    VALUES (?, ?, ?)
                    """,
                    (state, session_id, now),
                )

        return session_id, state

    def consume_state(self, state: str) -> str:
        now = self._datetime_to_text(datetime.now(timezone.utc))

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT session_id
                FROM github_oauth_states
                WHERE state = ? AND consumed_at IS NULL
                """,
                (state,),
            ).fetchone()

            if row is None:
                raise GitHubAuthError("Invalid or expired GitHub auth state.")

            with connection:
                connection.execute(
                    """
                    UPDATE github_oauth_states
                    SET consumed_at = ?
                    WHERE state = ?
                    """,
                    (now, state),
                )

        return str(row["session_id"])

    def save_authenticated_session(
        self,
        session_id: str,
        account_data: dict[str, Any],
        access_token: str,
        scopes: str | None,
    ) -> GitHubAccountResponse:
        now = self._datetime_to_text(datetime.now(timezone.utc))
        github_user_id = int(account_data["id"])

        with self._connect() as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO github_accounts (
                        github_user_id,
                        login,
                        name,
                        avatar_url,
                        html_url,
                        scopes,
                        access_token,
                        connected_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(github_user_id) DO UPDATE SET
                        login = excluded.login,
                        name = excluded.name,
                        avatar_url = excluded.avatar_url,
                        html_url = excluded.html_url,
                        scopes = excluded.scopes,
                        access_token = excluded.access_token,
                        updated_at = excluded.updated_at
                    """,
                    (
                        github_user_id,
                        account_data["login"],
                        account_data.get("name"),
                        account_data.get("avatar_url"),
                        account_data.get("html_url"),
                        scopes,
                        access_token,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO github_sessions (
                        session_id,
                        github_user_id,
                        created_at
                    )
                    VALUES (?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        github_user_id = excluded.github_user_id,
                        revoked_at = NULL
                    """,
                    (session_id, github_user_id, now),
                )

        session = self.get_session(session_id)

        if session is None:
            raise GitHubAuthError("Failed to persist GitHub session.")

        return session.account

    def get_session(self, session_id: str) -> StoredGitHubSession | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    s.session_id,
                    a.github_user_id,
                    a.login,
                    a.name,
                    a.avatar_url,
                    a.html_url,
                    a.scopes,
                    a.access_token,
                    a.connected_at
                FROM github_sessions s
                JOIN github_accounts a
                    ON a.github_user_id = s.github_user_id
                WHERE s.session_id = ?
                  AND s.revoked_at IS NULL
                """,
                (session_id,),
            ).fetchone()

        if row is None:
            return None

        return StoredGitHubSession(
            session_id=str(row["session_id"]),
            access_token=str(row["access_token"]),
            account=GitHubAccountResponse(
                github_user_id=int(row["github_user_id"]),
                login=str(row["login"]),
                name=row["name"],
                avatar_url=row["avatar_url"],
                html_url=row["html_url"],
                scopes=row["scopes"],
                connected_at=row["connected_at"],
            ),
        )

    def revoke_session(self, session_id: str) -> None:
        with self._connect() as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE github_sessions
                    SET revoked_at = ?
                    WHERE session_id = ?
                    """,
                    (
                        self._datetime_to_text(datetime.now(timezone.utc)),
                        session_id,
                    ),
                )

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            with connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS github_oauth_states (
                        state TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        consumed_at TEXT
                    );

                    CREATE TABLE IF NOT EXISTS github_accounts (
                        github_user_id INTEGER PRIMARY KEY,
                        login TEXT NOT NULL,
                        name TEXT,
                        avatar_url TEXT,
                        html_url TEXT,
                        scopes TEXT,
                        access_token TEXT NOT NULL,
                        connected_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS github_sessions (
                        session_id TEXT PRIMARY KEY,
                        github_user_id INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        revoked_at TEXT,
                        FOREIGN KEY(github_user_id)
                            REFERENCES github_accounts(github_user_id)
                            ON DELETE CASCADE
                    );
                    """
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _datetime_to_text(self, value: datetime) -> str:
        return value.isoformat()


class GitHubAuthService:
    """
    GitHub OAuth and read-only account/repository API integration.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = GitHubAuthStore(settings)

    def start_login(self) -> tuple[str, str, str]:
        if not self.settings.github_oauth_client_id:
            raise GitHubAuthError("GITHUB_OAUTH_CLIENT_ID is not configured.")

        session_id, state = self.store.create_pending_session()
        query = urlencode(
            {
                "client_id": self.settings.github_oauth_client_id,
                "redirect_uri": self.settings.github_oauth_redirect_uri,
                "scope": "repo read:user",
                "state": state,
            }
        )

        return f"https://github.com/login/oauth/authorize?{query}", session_id, state

    async def complete_callback(self, code: str, state: str) -> GitHubAccountResponse:
        if not self.settings.github_oauth_client_id:
            raise GitHubAuthError("GITHUB_OAUTH_CLIENT_ID is not configured.")

        if not self.settings.github_oauth_client_secret:
            raise GitHubAuthError("GITHUB_OAUTH_CLIENT_SECRET is not configured.")

        session_id = self.store.consume_state(state)

        token_payload = {
            "client_id": self.settings.github_oauth_client_id,
            "client_secret": self.settings.github_oauth_client_secret,
            "code": code,
            "redirect_uri": self.settings.github_oauth_redirect_uri,
            "state": state,
        }

        async with httpx.AsyncClient(timeout=30) as client:
            token_response = await client.post(
                "https://github.com/login/oauth/access_token",
                data=token_payload,
                headers={"Accept": "application/json"},
            )

        if token_response.status_code >= 400:
            raise GitHubAuthError(
                f"GitHub token exchange failed: {token_response.status_code}"
            )

        token_data = token_response.json()
        access_token = token_data.get("access_token")

        if not access_token:
            raise GitHubAuthError("GitHub token exchange did not return a token.")

        account_data = await self._get_user(access_token)
        scopes = token_response.headers.get("X-OAuth-Scopes") or token_data.get("scope")

        return self.store.save_authenticated_session(
            session_id=session_id,
            account_data=account_data,
            access_token=access_token,
            scopes=scopes,
        )

    def get_session(self, session_id: str | None) -> StoredGitHubSession:
        if not session_id:
            raise GitHubAuthError("GitHub authentication is required.")

        session = self.store.get_session(session_id)

        if session is None:
            raise GitHubAuthError("GitHub authentication is required.")

        return session

    def logout(self, session_id: str | None) -> None:
        if session_id:
            self.store.revoke_session(session_id)

    async def list_repositories(self, session_id: str | None) -> list[GitHubRepositorySummary]:
        session = self.get_session(session_id)
        repositories: list[GitHubRepositorySummary] = []
        url: str | None = (
            "https://api.github.com/user/repos?"
            "affiliation=owner,collaborator,organization_member&per_page=100&sort=updated"
        )

        async with httpx.AsyncClient(timeout=30) as client:
            while url:
                response = await client.get(url, headers=self._headers(session.access_token))

                if response.status_code >= 400:
                    raise GitHubAuthError(
                        redact_token(
                            f"GitHub repository list failed: "
                            f"{response.status_code} {response.text}",
                            session.access_token,
                        )
                    )

                for item in response.json():
                    repositories.append(self._map_repository(item))

                url = self._next_page_url(response.headers.get("Link"))

        return repositories

    async def _get_user(self, access_token: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                "https://api.github.com/user",
                headers=self._headers(access_token),
            )

        if response.status_code >= 400:
            raise GitHubAuthError(
                redact_token(
                    f"GitHub user lookup failed: {response.status_code} {response.text}",
                    access_token,
                )
            )

        return dict(response.json())

    def _headers(self, access_token: str) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {access_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _map_repository(self, item: dict[str, Any]) -> GitHubRepositorySummary:
        permissions = item.get("permissions") or {}
        can_read = bool(permissions.get("pull", True))
        can_push = bool(permissions.get("push", False) or permissions.get("admin", False))
        private = bool(item.get("private", False))

        return GitHubRepositorySummary(
            id=int(item["id"]),
            name=str(item["name"]),
            full_name=str(item["full_name"]),
            html_url=str(item["html_url"]),
            clone_url=str(item["clone_url"]),
            default_branch=str(item.get("default_branch") or self.settings.github_default_branch),
            private=private,
            visibility=str(item.get("visibility") or ("private" if private else "public")),
            can_read=can_read,
            can_push=can_push,
        )

    def _next_page_url(self, link_header: str | None) -> str | None:
        if not link_header:
            return None

        for part in link_header.split(","):
            section = part.strip()
            if 'rel="next"' not in section:
                continue

            start = section.find("<")
            end = section.find(">")

            if start != -1 and end != -1 and start < end:
                return section[start + 1 : end]

        return None
