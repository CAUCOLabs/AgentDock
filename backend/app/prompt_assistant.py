import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import Settings
from app.models import (
    PromptAssistantChangeScope,
    PromptAssistantProvider,
    PromptAssistantProviderStatus,
    PromptAssistantRefineRequest,
    PromptAssistantRefineResponse,
)

logger = logging.getLogger(__name__)

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s]+"),
    re.compile(r"(?i)((?:id_token|access_token|refresh_token)=)[^&\s]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s,;]+"),
]

_PROMPT_PROVIDER_LABELS = {
    PromptAssistantProvider.openai: "ChatGPT / OpenAI",
    PromptAssistantProvider.anthropic: "Claude / Anthropic",
}


@dataclass(frozen=True)
class PromptAssistantCredential:
    provider: PromptAssistantProvider
    api_key: str
    connected_at: datetime
    updated_at: datetime
    last_verified_at: datetime | None
    last_error: str | None


class PromptAssistantProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_type: str | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(redact_secret_text(message))
        self.status_code = status_code
        self.error_type = redact_secret_text(error_type) if error_type else None
        self.error_code = redact_secret_text(error_code) if error_code else None


def redact_secret_text(message: str) -> str:
    redacted = message
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(
            r"\1[REDACTED]" if pattern.groups else "[REDACTED]",
            redacted,
        )
    return redacted


class PromptAssistantCredentialStore:
    """
    SQLite-backed server-side credential storage for Prompt Assistant providers.

    Keys are intentionally isolated from task logs and API responses. AisoFlow
    does not currently have a general encryption layer, so callers must never
    expose raw credential rows outside this class.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db_path = settings.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    def list_statuses(self) -> list[PromptAssistantProviderStatus]:
        credentials = {
            credential.provider: credential
            for credential in (
                self.get_credential(PromptAssistantProvider.openai),
                self.get_credential(PromptAssistantProvider.anthropic),
            )
            if credential is not None
        }
        return [
            self._status_for_provider(provider, credentials.get(provider))
            for provider in (PromptAssistantProvider.openai, PromptAssistantProvider.anthropic)
        ]

    def connect(self, provider: PromptAssistantProvider, api_key: str) -> PromptAssistantProviderStatus:
        self._validate_connectable_provider(provider)
        now = self._datetime_to_text(datetime.now(timezone.utc))
        with self._connect() as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO prompt_assistant_provider_credentials (
                        provider,
                        api_key,
                        connected_at,
                        updated_at,
                        last_verified_at,
                        last_error
                    )
                    VALUES (?, ?, ?, ?, NULL, NULL)
                    ON CONFLICT(provider) DO UPDATE SET
                        api_key = excluded.api_key,
                        updated_at = excluded.updated_at,
                        last_verified_at = NULL,
                        last_error = NULL
                    """,
                    (provider.value, api_key, now, now),
                )

        credential = self.get_credential(provider)
        return self._status_for_provider(provider, credential)

    def disconnect(self, provider: PromptAssistantProvider) -> PromptAssistantProviderStatus:
        self._validate_connectable_provider(provider)
        with self._connect() as connection:
            with connection:
                connection.execute(
                    "DELETE FROM prompt_assistant_provider_credentials WHERE provider = ?",
                    (provider.value,),
                )
        return self._status_for_provider(provider, None)

    def mark_test_result(
        self,
        provider: PromptAssistantProvider,
        *,
        ok: bool,
        error: str | None = None,
    ) -> PromptAssistantProviderStatus:
        self._validate_connectable_provider(provider)
        credential = self.get_credential(provider)
        if credential is None:
            return self._status_for_provider(
                provider,
                None,
                error="Provider is not connected.",
            )

        now = self._datetime_to_text(datetime.now(timezone.utc))
        sanitized_error = redact_secret_text(error or "") if error else None
        with self._connect() as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE prompt_assistant_provider_credentials
                    SET last_verified_at = ?,
                        last_error = ?
                    WHERE provider = ?
                    """,
                    (now if ok else credential.last_verified_at and self._datetime_to_text(credential.last_verified_at), None if ok else sanitized_error, provider.value),
                )

        return self._status_for_provider(
            provider,
            self.get_credential(provider),
            error=None if ok else sanitized_error,
        )

    def get_credential(
        self,
        provider: PromptAssistantProvider,
    ) -> PromptAssistantCredential | None:
        self._validate_connectable_provider(provider)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT provider, api_key, connected_at, updated_at, last_verified_at, last_error
                FROM prompt_assistant_provider_credentials
                WHERE provider = ?
                """,
                (provider.value,),
            ).fetchone()

        if row is None:
            return None

        return PromptAssistantCredential(
            provider=PromptAssistantProvider(row["provider"]),
            api_key=str(row["api_key"]),
            connected_at=self._datetime_from_text(row["connected_at"]),
            updated_at=self._datetime_from_text(row["updated_at"]),
            last_verified_at=(
                self._datetime_from_text(row["last_verified_at"])
                if row["last_verified_at"]
                else None
            ),
            last_error=(
                redact_secret_text(str(row["last_error"]))
                if row["last_error"]
                else None
            ),
        )

    def _status_for_provider(
        self,
        provider: PromptAssistantProvider,
        credential: PromptAssistantCredential | None,
        *,
        error: str | None = None,
    ) -> PromptAssistantProviderStatus:
        status_error = error or (credential.last_error if credential else None)
        return PromptAssistantProviderStatus(
            provider=provider,
            connected=credential is not None,
            label=_PROMPT_PROVIDER_LABELS[provider],
            last_verified_at=credential.last_verified_at if credential else None,
            error=redact_secret_text(status_error) if status_error else None,
        )

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS prompt_assistant_provider_credentials (
                        provider TEXT PRIMARY KEY,
                        api_key TEXT NOT NULL,
                        connected_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_verified_at TEXT,
                        last_error TEXT
                    )
                    """
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _datetime_to_text(self, value: datetime) -> str:
        return value.isoformat()

    def _datetime_from_text(self, value: str) -> datetime:
        return datetime.fromisoformat(value)

    def _validate_connectable_provider(self, provider: PromptAssistantProvider) -> None:
        if provider not in _PROMPT_PROVIDER_LABELS:
            raise ValueError("Prompt Assistant provider must be openai or anthropic.")


def refine_prompt(
    payload: PromptAssistantRefineRequest,
    settings: Settings,
) -> PromptAssistantRefineResponse:
    provider = payload.provider

    if provider == PromptAssistantProvider.openai:
        api_key = _api_key_for_provider(settings, provider)
        if not api_key:
            return fallback_response(payload)
        return _refine_with_openai(payload, api_key, settings.prompt_assistant_openai_model)

    if provider == PromptAssistantProvider.anthropic:
        api_key = _api_key_for_provider(settings, provider)
        if not api_key:
            return fallback_response(payload)
        return _refine_with_anthropic(payload, api_key, settings.prompt_assistant_anthropic_model)

    return fallback_response(payload)


def prompt_assistant_provider_statuses(settings: Settings) -> list[PromptAssistantProviderStatus]:
    return PromptAssistantCredentialStore(settings).list_statuses()


def connect_prompt_assistant_provider(
    settings: Settings,
    provider: PromptAssistantProvider,
    api_key: str,
) -> PromptAssistantProviderStatus:
    return PromptAssistantCredentialStore(settings).connect(provider, api_key)


def disconnect_prompt_assistant_provider(
    settings: Settings,
    provider: PromptAssistantProvider,
) -> PromptAssistantProviderStatus:
    return PromptAssistantCredentialStore(settings).disconnect(provider)


def test_prompt_assistant_provider(
    settings: Settings,
    provider: PromptAssistantProvider,
) -> PromptAssistantProviderStatus:
    store = PromptAssistantCredentialStore(settings)
    credential = store.get_credential(provider)
    if credential is None:
        return store.mark_test_result(provider, ok=False, error="Provider is not connected.")

    try:
        if provider == PromptAssistantProvider.openai:
            _test_openai_key(credential.api_key, settings.prompt_assistant_openai_model)
        elif provider == PromptAssistantProvider.anthropic:
            _test_anthropic_key(credential.api_key, settings.prompt_assistant_anthropic_model)
        else:
            raise ValueError("Prompt Assistant provider must be openai or anthropic.")
    except Exception as exc:
        logger.warning(
            "Prompt assistant provider test failed for %s: %s",
            provider.value,
            redact_secret_text(str(exc)),
        )
        return store.mark_test_result(provider, ok=False, error=str(exc))

    return store.mark_test_result(provider, ok=True)


def fallback_response(payload: PromptAssistantRefineRequest) -> PromptAssistantRefineResponse:
    scope = _recommended_scope(payload)
    target = _clean_optional(payload.target_file)
    repo_name = _clean_optional(payload.repo_name) or "the selected repository"
    rough_idea = payload.rough_idea.strip()
    title = _title_from_idea(rough_idea)

    scope_copy = {
        PromptAssistantChangeScope.single_file: (
            f"Focus changes on `{target}` only."
            if target
            else "Keep the change limited to the smallest single file that can satisfy the request."
        ),
        PromptAssistantChangeScope.related_files: (
            "Modify the target file and directly related modules, tests, or configuration only."
        ),
        PromptAssistantChangeScope.whole_project: (
            "Repository-wide creation and modification is allowed when needed to complete the task."
        ),
    }[scope]

    target_copy = f"\nTarget file: `{target}`." if target else ""
    readme_excerpt = _clean_optional(payload.existing_readme_excerpt)
    readme_copy = (
        f"\nRepository context excerpt:\n{readme_excerpt[:1200].strip()}"
        if readme_excerpt
        else ""
    )

    refined_prompt = "\n".join(
        [
            f"Objective: {rough_idea}",
            "",
            f"Repository: {repo_name}.{target_copy}{readme_copy}",
            "",
            f"Scope: {scope_copy}",
            "",
            "Constraints:",
            "- Preserve existing behavior unless the requested change requires otherwise.",
            "- Follow the existing architecture, style, naming, and test patterns.",
            "- Do not modify unrelated files.",
            "- Avoid broad refactors, dependency churn, generated-file noise, or formatting-only changes unless required.",
            "",
            "Expected output:",
            "- Implement the requested change in the smallest coherent set of files.",
            "- Add or update focused tests when the change affects behavior.",
            "- Summarize what changed, how it was verified, and any remaining risks.",
            "",
            "Safety rules:",
            "- Inspect relevant files before editing.",
            "- Keep secrets and credentials out of source code and logs.",
            "- Do not push, deploy, or open a pull request unless explicitly asked.",
        ]
    )

    return PromptAssistantRefineResponse(
        provider=PromptAssistantProvider.fallback,
        title=title,
        recommended_scope=scope,
        refined_prompt=refined_prompt,
        safety_notes=[
            "Fallback prompt template used; no external assistant provider was called.",
            "Codex will not run until the user launches the task.",
            "Do not modify unrelated files.",
        ],
        used_fallback=True,
    )


def _refine_with_openai(
    payload: PromptAssistantRefineRequest,
    api_key: str,
    model: str,
) -> PromptAssistantRefineResponse:
    try:
        response = httpx.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "input": _provider_prompt(payload),
                "temperature": 0.2,
            },
            timeout=20,
        )
        _raise_for_provider_error(response, provider=PromptAssistantProvider.openai)
        return _response_from_provider_json(
            payload,
            PromptAssistantProvider.openai,
            response.json(),
        )
    except Exception as exc:
        logger.warning(
            "OpenAI prompt assistant failed; using fallback: %s",
            redact_secret_text(str(exc)),
        )
        return fallback_response(payload)


def _refine_with_anthropic(
    payload: PromptAssistantRefineRequest,
    api_key: str,
    model: str,
) -> PromptAssistantRefineResponse:
    try:
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 1400,
                "temperature": 0.2,
                "messages": [{"role": "user", "content": _provider_prompt(payload)}],
            },
            timeout=20,
        )
        _raise_for_provider_error(response, provider=PromptAssistantProvider.anthropic)
        return _response_from_provider_json(
            payload,
            PromptAssistantProvider.anthropic,
            response.json(),
        )
    except Exception as exc:
        logger.warning(
            "Anthropic prompt assistant failed; using fallback: %s",
            redact_secret_text(str(exc)),
        )
        return fallback_response(payload)


def _api_key_for_provider(
    settings: Settings,
    provider: PromptAssistantProvider,
) -> str | None:
    try:
        credential = PromptAssistantCredentialStore(settings).get_credential(provider)
        if credential and credential.api_key:
            return credential.api_key
    except Exception as exc:
        logger.warning(
            "Prompt assistant credential lookup failed for %s: %s",
            provider.value,
            redact_secret_text(str(exc)),
        )

    if provider == PromptAssistantProvider.openai:
        return settings.openai_api_key
    if provider == PromptAssistantProvider.anthropic:
        return settings.anthropic_api_key
    return None


def _test_openai_key(api_key: str, model: str) -> None:
    response = httpx.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "input": "Say OK",
        },
        timeout=15,
    )
    _raise_for_provider_error(response, provider=PromptAssistantProvider.openai)


def _test_anthropic_key(api_key: str, model: str) -> None:
    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 8,
            "messages": [{"role": "user", "content": "Reply with OK."}],
        },
        timeout=15,
    )
    _raise_for_provider_error(response, provider=PromptAssistantProvider.anthropic)


def _raise_for_provider_error(
    response: httpx.Response,
    *,
    provider: PromptAssistantProvider,
) -> None:
    status_code = getattr(response, "status_code", 200)
    if status_code < 400:
        return

    message, error_type, error_code = _provider_error_details(response)
    detail = _format_provider_error_detail(
        provider=provider,
        status_code=status_code,
        message=message,
        error_type=error_type,
        error_code=error_code,
    )
    raise PromptAssistantProviderError(
        detail,
        status_code=status_code,
        error_type=error_type,
        error_code=error_code,
    )


def _provider_error_details(response: httpx.Response) -> tuple[str, str | None, str | None]:
    raw_text = redact_secret_text(getattr(response, "text", "") or "")
    try:
        data = response.json()
    except Exception:
        return raw_text or "Provider request failed.", None, None

    if not isinstance(data, dict):
        return raw_text or "Provider request failed.", None, None

    error = data.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or raw_text or "Provider request failed.")
        error_type = error.get("type")
        error_code = error.get("code")
    else:
        message = str(data.get("message") or raw_text or "Provider request failed.")
        error_type = data.get("type")
        error_code = data.get("code")

    return (
        redact_secret_text(message),
        redact_secret_text(str(error_type)) if error_type is not None else None,
        redact_secret_text(str(error_code)) if error_code is not None else None,
    )


def _format_provider_error_detail(
    *,
    provider: PromptAssistantProvider,
    status_code: int,
    message: str,
    error_type: str | None,
    error_code: str | None,
) -> str:
    if provider == PromptAssistantProvider.openai and _is_openai_model_unavailable(
        message=message,
        error_type=error_type,
        error_code=error_code,
    ):
        return (
            "OpenAI key is valid, but model is not available. "
            "Check PROMPT_ASSISTANT_OPENAI_MODEL."
        )

    parts = [f"status_code={status_code}", f"message={message}"]
    if error_type:
        parts.append(f"type={error_type}")
    if error_code:
        parts.append(f"code={error_code}")
    return redact_secret_text("; ".join(parts))


def _is_openai_model_unavailable(
    *,
    message: str,
    error_type: str | None,
    error_code: str | None,
) -> bool:
    values = " ".join(
        value.lower()
        for value in (message, error_type, error_code)
        if value
    )
    return (
        "model_not_found" in values
        or "unsupported model" in values
        or "model is not available" in values
        or "model not found" in values
    )


def _provider_prompt(payload: PromptAssistantRefineRequest) -> str:
    scope = _recommended_scope(payload).value
    return (
        "Create a Codex-ready task prompt from the rough idea below. "
        "Return JSON only with keys: title, recommended_scope, refined_prompt, safety_notes. "
        "recommended_scope must be one of single_file, related_files, whole_project. "
        "The refined_prompt must include objective, scope, constraints, expected output, safety rules, "
        "and the phrase 'Do not modify unrelated files.' "
        "Do not include markdown fences.\n\n"
        f"Rough idea: {payload.rough_idea.strip()}\n"
        f"Repository name: {_clean_optional(payload.repo_name) or 'unknown'}\n"
        f"Target file: {_clean_optional(payload.target_file) or 'none'}\n"
        f"Requested scope: {scope}\n"
        f"README excerpt: {_clean_optional(payload.existing_readme_excerpt) or 'none'}"
    )


def _response_from_provider_json(
    payload: PromptAssistantRefineRequest,
    provider: PromptAssistantProvider,
    data: dict[str, Any],
) -> PromptAssistantRefineResponse:
    text = _extract_provider_text(data)
    parsed = json.loads(_strip_json_fence(text))
    scope = _parse_scope(parsed.get("recommended_scope")) or _recommended_scope(payload)
    refined_prompt = str(parsed.get("refined_prompt") or "").strip()
    if "do not modify unrelated files" not in refined_prompt.lower():
        refined_prompt = f"{refined_prompt}\n\nSafety: Do not modify unrelated files."

    if not refined_prompt:
        raise ValueError("Provider response did not include a refined prompt.")

    safety_notes = parsed.get("safety_notes")
    if not isinstance(safety_notes, list):
        safety_notes = ["Codex will not run until the user launches the task."]

    return PromptAssistantRefineResponse(
        provider=provider,
        title=str(parsed.get("title") or _title_from_idea(payload.rough_idea)).strip(),
        recommended_scope=scope,
        refined_prompt=refined_prompt,
        safety_notes=[str(note) for note in safety_notes],
        used_fallback=False,
    )


def _extract_provider_text(data: dict[str, Any]) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]

    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        return part["text"]

    content = data.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                return part["text"]

    raise ValueError("Provider response did not include text.")


def _strip_json_fence(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _recommended_scope(
    payload: PromptAssistantRefineRequest,
) -> PromptAssistantChangeScope:
    if payload.change_scope:
        return payload.change_scope
    if _clean_optional(payload.target_file):
        return PromptAssistantChangeScope.single_file
    return PromptAssistantChangeScope.related_files


def _parse_scope(value: Any) -> PromptAssistantChangeScope | None:
    try:
        return PromptAssistantChangeScope(str(value))
    except ValueError:
        return None


def _title_from_idea(rough_idea: str) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-]*", rough_idea.strip())
    if not words:
        return "Codex Task Prompt"
    return " ".join(words[:8]).capitalize()


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
