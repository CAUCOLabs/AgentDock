import json
import logging
import re
from typing import Any

import httpx

from app.config import Settings
from app.models import (
    PromptAssistantChangeScope,
    PromptAssistantProvider,
    PromptAssistantRefineRequest,
    PromptAssistantRefineResponse,
)

logger = logging.getLogger(__name__)

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s,;]+"),
]


def redact_secret_text(message: str) -> str:
    redacted = message
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(
            r"\1[REDACTED]" if pattern.groups else "[REDACTED]",
            redacted,
        )
    return redacted


def refine_prompt(
    payload: PromptAssistantRefineRequest,
    settings: Settings,
) -> PromptAssistantRefineResponse:
    provider = payload.provider

    if provider == PromptAssistantProvider.openai:
        if not settings.openai_api_key:
            return fallback_response(payload)
        return _refine_with_openai(payload, settings)

    if provider == PromptAssistantProvider.anthropic:
        if not settings.anthropic_api_key:
            return fallback_response(payload)
        return _refine_with_anthropic(payload, settings)

    return fallback_response(payload)


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
    settings: Settings,
) -> PromptAssistantRefineResponse:
    try:
        response = httpx.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4.1-mini",
                "input": _provider_prompt(payload),
                "temperature": 0.2,
            },
            timeout=20,
        )
        response.raise_for_status()
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
    settings: Settings,
) -> PromptAssistantRefineResponse:
    try:
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": settings.anthropic_api_key or "",
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": "claude-3-5-haiku-latest",
                "max_tokens": 1400,
                "temperature": 0.2,
                "messages": [{"role": "user", "content": _provider_prompt(payload)}],
            },
            timeout=20,
        )
        response.raise_for_status()
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
