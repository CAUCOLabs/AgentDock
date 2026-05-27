import hashlib
import secrets
import sqlite3
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from time import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.config import Settings
from app.models import (
    GitHubAccountResponse,
    GitHubInstallationResponse,
    GitHubRepositorySummary,
)

logger = logging.getLogger(__name__)


class GitHubAuthError(Exception):
    """
    Raised when GitHub authentication or authenticated API access fails.
    """


GITHUB_APP_NOT_CONFIGURED = "GitHub App is not configured"
GITHUB_APP_SESSION_MISSING = (
    "GitHub App session is missing. Reconnect GitHub on this device "
    "if the app is already installed."
)
GITHUB_APP_INSTALLATION_REQUIRED = (
    "GitHub App installation is required. Complete the installation in GitHub, "
    "then return to AisoFlow."
)


def github_auth_correlation_id(value: str | None) -> str:
    if not value:
        return "missing"

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def log_safe_url(url: str) -> str:
    split_url = urlsplit(url)
    redacted_query = urlencode(
        [(key, "[REDACTED]") for key, _value in parse_qsl(split_url.query)]
    )
    return urlunsplit(
        (
            split_url.scheme,
            split_url.netloc,
            split_url.path,
            redacted_query,
            split_url.fragment,
        )
    )


@dataclass(frozen=True)
class StoredGitHubSession:
    session_id: str
    access_token: str
    account: GitHubAccountResponse


@dataclass(frozen=True)
class StoredGitHubAppSession:
    session_id: str
    installation: GitHubInstallationResponse


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

    def save_app_installation(
        self,
        session_id: str,
        installation_id: int,
        account_login: str,
        account_type: str,
        selected_repositories: list[str],
        permissions: dict[str, str],
    ) -> GitHubInstallationResponse:
        now = self._datetime_to_text(datetime.now(timezone.utc))

        with self._connect() as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO github_app_installations (
                        installation_id,
                        account_login,
                        account_type,
                        selected_repositories_json,
                        permissions_json,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(installation_id) DO UPDATE SET
                        account_login = excluded.account_login,
                        account_type = excluded.account_type,
                        selected_repositories_json = excluded.selected_repositories_json,
                        permissions_json = excluded.permissions_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        installation_id,
                        account_login,
                        account_type,
                        json.dumps(selected_repositories),
                        json.dumps(permissions, sort_keys=True),
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO github_app_sessions (
                        session_id,
                        installation_id,
                        created_at
                    )
                    VALUES (?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        installation_id = excluded.installation_id,
                        revoked_at = NULL
                    """,
                    (session_id, installation_id, now),
                )

        installation = self.get_app_session(session_id)

        if installation is None:
            raise GitHubAuthError("Failed to persist GitHub App installation.")

        return installation.installation

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

    def get_pending_session_id(self, state: str) -> str:
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

        return str(row["session_id"])

    def has_pending_session(self, session_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM github_oauth_states
                WHERE session_id = ? AND consumed_at IS NULL
                """,
                (session_id,),
            ).fetchone()

        return row is not None

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

    def get_app_session(self, session_id: str) -> StoredGitHubAppSession | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    s.session_id,
                    i.installation_id,
                    i.account_login,
                    i.account_type,
                    i.selected_repositories_json,
                    i.permissions_json,
                    i.created_at,
                    i.updated_at
                FROM github_app_sessions s
                JOIN github_app_installations i
                    ON i.installation_id = s.installation_id
                WHERE s.session_id = ?
                  AND s.revoked_at IS NULL
                """,
                (session_id,),
            ).fetchone()

        if row is None:
            return None

        return StoredGitHubAppSession(
            session_id=str(row["session_id"]),
            installation=GitHubInstallationResponse(
                installation_id=int(row["installation_id"]),
                account_login=str(row["account_login"]),
                account_type=str(row["account_type"]),
                selected_repositories=json.loads(
                    row["selected_repositories_json"] or "[]"
                ),
                permissions=json.loads(row["permissions_json"] or "{}"),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            ),
        )

    def list_app_installations(self) -> list[GitHubInstallationResponse]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM github_app_installations
                ORDER BY updated_at DESC
                """
            ).fetchall()

        return [
            GitHubInstallationResponse(
                installation_id=int(row["installation_id"]),
                account_login=str(row["account_login"]),
                account_type=str(row["account_type"]),
                selected_repositories=json.loads(
                    row["selected_repositories_json"] or "[]"
                ),
                permissions=json.loads(row["permissions_json"] or "{}"),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

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
                connection.execute(
                    """
                    UPDATE github_app_sessions
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

                    CREATE TABLE IF NOT EXISTS github_app_installations (
                        installation_id INTEGER PRIMARY KEY,
                        account_login TEXT NOT NULL,
                        account_type TEXT NOT NULL,
                        selected_repositories_json TEXT NOT NULL DEFAULT '[]',
                        permissions_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS github_app_sessions (
                        session_id TEXT PRIMARY KEY,
                        installation_id INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        revoked_at TEXT,
                        FOREIGN KEY(installation_id)
                            REFERENCES github_app_installations(installation_id)
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
    GitHub App, OAuth fallback, and read-only repository API integration.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = GitHubAuthStore(settings)

    def start_app_installation(self) -> tuple[str, str, str]:
        install_url = self._app_install_url()

        session_id, state = self.store.create_pending_session()
        split_url = urlsplit(install_url)
        query_params = [
            (key, value)
            for key, value in parse_qsl(split_url.query, keep_blank_values=True)
            if key != "state"
        ]
        query_params.append(("state", state))
        auth_url = urlunsplit(
            (
                split_url.scheme,
                split_url.netloc,
                split_url.path,
                urlencode(query_params),
                split_url.fragment,
            )
        )
        logger.info(
            "GitHub App installation started: session_id=%s state_id=%s auth_url=%s",
            github_auth_correlation_id(session_id),
            github_auth_correlation_id(state),
            log_safe_url(auth_url),
        )

        return auth_url, session_id, state

    async def complete_app_callback(
        self,
        installation_id: int,
        state: str,
    ) -> GitHubInstallationResponse:
        state_id = github_auth_correlation_id(state)
        try:
            session_id = self.store.get_pending_session_id(state)
        except GitHubAuthError:
            logger.warning(
                "GitHub App callback state lookup failed: installation_id=%s state_id=%s",
                installation_id,
                state_id,
            )
            raise

        logger.info(
            "GitHub App callback state matched: installation_id=%s state_id=%s session_id=%s",
            installation_id,
            state_id,
            github_auth_correlation_id(session_id),
        )
        installation_data = await self.get_app_installation_metadata(installation_id)
        installation_token = await self.create_installation_token(installation_id)
        repositories = await self._list_installation_repository_data(installation_token)
        selected_repositories = [
            str(item["full_name"])
            for item in repositories
            if item.get("full_name")
        ]
        account = installation_data.get("account") or {}
        consumed_session_id = self.store.consume_state(state)

        if consumed_session_id != session_id:
            raise GitHubAuthError("Invalid or expired GitHub auth state.")

        installation = self.store.save_app_installation(
            session_id=session_id,
            installation_id=installation_id,
            account_login=str(account.get("login") or "unknown"),
            account_type=str(account.get("type") or "unknown"),
            selected_repositories=selected_repositories,
            permissions={
                str(key): str(value)
                for key, value in (installation_data.get("permissions") or {}).items()
            },
        )
        logger.info(
            "GitHub App installation linked: installation_id=%s state_id=%s session_id=%s",
            installation_id,
            state_id,
            github_auth_correlation_id(session_id),
        )
        return installation

    def get_app_session(
        self,
        session_id: str | None,
    ) -> StoredGitHubAppSession:
        if not session_id:
            raise GitHubAuthError(GITHUB_APP_SESSION_MISSING)

        session = self.store.get_app_session(session_id)

        if session is None:
            if self.store.has_pending_session(session_id):
                raise GitHubAuthError(GITHUB_APP_INSTALLATION_REQUIRED)

            raise GitHubAuthError(GITHUB_APP_SESSION_MISSING)

        return session

    def list_app_installations(self) -> list[GitHubInstallationResponse]:
        return self.store.list_app_installations()

    async def list_app_repositories(
        self,
        session_id: str | None,
    ) -> list[GitHubRepositorySummary]:
        session = self.get_app_session(session_id)
        installation_token = await self.create_installation_token(
            session.installation.installation_id
        )
        repository_data = await self._list_installation_repository_data(
            installation_token
        )
        return [self._map_repository(item) for item in repository_data]

    async def token_for_session(self, session_id: str | None) -> str | None:
        if not session_id:
            return None

        app_session = self.store.get_app_session(session_id)

        if app_session is not None:
            return await self.create_installation_token(
                app_session.installation.installation_id
            )

        oauth_session = self.store.get_session(session_id)

        if oauth_session is not None and self.settings.is_development:
            return oauth_session.access_token

        raise GitHubAuthError("GitHub authentication is required.")

    async def create_installation_token(self, installation_id: int) -> str:
        self._require_app_token_config()
        app_jwt = self._create_app_jwt()

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"https://api.github.com/app/installations/{installation_id}/access_tokens",
                headers=self._headers(app_jwt),
            )

        if response.status_code >= 400:
            raise GitHubAuthError(
                redact_token(
                    f"GitHub installation token request failed: "
                    f"{response.status_code} {response.text}",
                    app_jwt,
                )
            )

        token = response.json().get("token")

        if not token:
            raise GitHubAuthError("GitHub installation token response was empty.")

        return str(token)

    async def get_app_installation_metadata(
        self,
        installation_id: int,
    ) -> dict[str, Any]:
        self._require_app_token_config()
        app_jwt = self._create_app_jwt()

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"https://api.github.com/app/installations/{installation_id}",
                headers=self._headers(app_jwt),
            )

        if response.status_code >= 400:
            raise GitHubAuthError(
                redact_token(
                    f"GitHub installation lookup failed: "
                    f"{response.status_code} {response.text}",
                    app_jwt,
                )
            )

        return dict(response.json())

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

    async def _list_installation_repository_data(
        self,
        installation_token: str,
    ) -> list[dict[str, Any]]:
        repositories: list[dict[str, Any]] = []
        url: str | None = "https://api.github.com/installation/repositories?per_page=100"

        async with httpx.AsyncClient(timeout=30) as client:
            while url:
                response = await client.get(
                    url,
                    headers=self._headers(installation_token),
                )

                if response.status_code >= 400:
                    raise GitHubAuthError(
                        redact_token(
                            f"GitHub installation repository list failed: "
                            f"{response.status_code} {response.text}",
                            installation_token,
                        )
                    )

                payload = response.json()
                repositories.extend(payload.get("repositories", []))
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

    def _create_app_jwt(self) -> str:
        self._require_app_token_config()

        try:
            import jwt
        except ImportError as exc:
            raise GitHubAuthError(
                "PyJWT[crypto] is required for GitHub App authentication."
            ) from exc

        try:
            private_key = self.settings.github_app_private_key_path.read_text(
                encoding="utf-8"
            )
        except OSError as exc:
            raise GitHubAuthError(
                "Failed to read GITHUB_APP_PRIVATE_KEY_PATH."
            ) from exc

        now = int(time())
        return str(
            jwt.encode(
                {
                    "iat": now - 60,
                    "exp": now + 540,
                    "iss": self.settings.github_app_id,
                },
                private_key,
                algorithm="RS256",
            )
        )

    def _app_install_url(self) -> str:
        if self.settings.github_app_install_url:
            return self.settings.github_app_install_url

        if self.settings.github_app_slug:
            return f"https://github.com/apps/{self.settings.github_app_slug}/installations/new"

        raise GitHubAuthError(GITHUB_APP_NOT_CONFIGURED)

    def _require_app_token_config(self) -> None:
        if not self.settings.github_app_id:
            raise GitHubAuthError(GITHUB_APP_NOT_CONFIGURED)

        if not self.settings.github_app_private_key_path:
            raise GitHubAuthError(GITHUB_APP_NOT_CONFIGURED)

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
