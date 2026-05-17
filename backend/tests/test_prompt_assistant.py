from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app


class PromptAssistantTests(unittest.TestCase):
    def test_provider_status_disconnected_by_default(self) -> None:
        response = self._client_without_keys().get("/prompt-assistant/providers")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            [(item["provider"], item["connected"]) for item in payload],
            [("openai", False), ("anthropic", False)],
        )
        self.assertNotIn("api_key", response.text)

    def test_connect_stores_metadata_but_never_returns_raw_key(self) -> None:
        secret = "sk-testsecret123456789"
        client = self._client_without_keys()

        response = client.post(
            "/prompt-assistant/providers/connect",
            json={"provider": "openai", "api_key": secret},
        )
        status_response = client.get("/prompt-assistant/providers")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["connected"])
        self.assertEqual(response.json()["provider"], "openai")
        self.assertNotIn(secret, response.text)
        self.assertNotIn(secret, status_response.text)

    def test_disconnect_clears_provider(self) -> None:
        client = self._client_without_keys()
        client.post(
            "/prompt-assistant/providers/connect",
            json={"provider": "openai", "api_key": "sk-testsecret123456789"},
        )

        response = client.post(
            "/prompt-assistant/providers/disconnect",
            json={"provider": "openai"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["connected"])

    def test_refine_uses_stored_openai_key(self) -> None:
        secret = "sk-openaitest123456789"
        client = self._client_without_keys()
        client.post(
            "/prompt-assistant/providers/connect",
            json={"provider": "openai", "api_key": secret},
        )

        with patch("app.prompt_assistant.httpx.post") as post:
            post.return_value = self._provider_response(
                {"output_text": self._provider_payload("OpenAI generated prompt")}
            )
            response = client.post(
                "/prompt-assistant/refine",
                json={"provider": "openai", "rough_idea": "add account export"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["used_fallback"])
        self.assertEqual(response.json()["provider"], "openai")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], f"Bearer {secret}")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "gpt-5.5")
        self.assertNotIn(secret, response.text)

    def test_refine_uses_stored_anthropic_key(self) -> None:
        secret = "sk-ant-testsecret123456789"
        client = self._client_without_keys()
        client.post(
            "/prompt-assistant/providers/connect",
            json={"provider": "anthropic", "api_key": secret},
        )

        with patch("app.prompt_assistant.httpx.post") as post:
            post.return_value = self._provider_response(
                {"content": [{"type": "text", "text": self._provider_payload("Claude generated prompt")}]}
            )
            response = client.post(
                "/prompt-assistant/refine",
                json={"provider": "anthropic", "rough_idea": "add usage chart"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["used_fallback"])
        self.assertEqual(response.json()["provider"], "anthropic")
        self.assertEqual(post.call_args.kwargs["headers"]["x-api-key"], secret)
        self.assertNotIn(secret, response.text)

    def test_fallback_works_without_keys(self) -> None:
        response = self._client_without_keys().post(
            "/prompt-assistant/refine",
            json={
                "provider": "fallback",
                "rough_idea": "add validation to the checkout form",
                "repo_name": "shop/app",
                "target_file": "src/checkout.ts",
                "change_scope": "single_file",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["provider"], "fallback")
        self.assertEqual(payload["recommended_scope"], "single_file")
        self.assertTrue(payload["used_fallback"])
        self.assertIn("Objective:", payload["refined_prompt"])
        self.assertIn("Do not modify unrelated files", payload["refined_prompt"])
        self.assertIn("src/checkout.ts", payload["refined_prompt"])

    def test_openai_missing_key_falls_back_safely(self) -> None:
        response = self._client_without_keys().post(
            "/prompt-assistant/refine",
            json={
                "provider": "openai",
                "rough_idea": "write tests for billing retries",
                "change_scope": "related_files",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["provider"], "fallback")
        self.assertEqual(payload["recommended_scope"], "related_files")
        self.assertTrue(payload["used_fallback"])

    def test_anthropic_missing_key_falls_back_safely(self) -> None:
        response = self._client_without_keys().post(
            "/prompt-assistant/refine",
            json={
                "provider": "anthropic",
                "rough_idea": "create onboarding settings page",
                "change_scope": "whole_project",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["provider"], "fallback")
        self.assertEqual(payload["recommended_scope"], "whole_project")
        self.assertTrue(payload["used_fallback"])
        self.assertIn("Repository-wide", payload["refined_prompt"])

    def test_bad_provider_key_test_returns_sanitized_failure(self) -> None:
        secret = "sk-testsecret123456789"
        client = self._client_without_keys()
        client.post(
            "/prompt-assistant/providers/connect",
            json={"provider": "openai", "api_key": secret},
        )

        with self.assertLogs("app.prompt_assistant", level="WARNING"), patch(
            "app.prompt_assistant.httpx.post",
            side_effect=RuntimeError(f"invalid api_key={secret}"),
        ):
            response = client.post(
                "/prompt-assistant/providers/test",
                json={"provider": "openai"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["connected"])
        self.assertIn("[REDACTED]", payload["error"])
        self.assertNotIn(secret, response.text)

    def test_openai_provider_test_succeeds_with_minimal_responses_payload(self) -> None:
        secret = "sk-testsecret123456789"
        client = self._client_without_keys()
        client.post(
            "/prompt-assistant/providers/connect",
            json={"provider": "openai", "api_key": secret},
        )

        with patch("app.prompt_assistant.httpx.post") as post:
            post.return_value = self._provider_response({"output_text": "OK"})
            response = client.post(
                "/prompt-assistant/providers/test",
                json={"provider": "openai"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["connected"])
        self.assertIsNone(payload["error"])
        self.assertIsNotNone(payload["last_verified_at"])
        self.assertEqual(
            post.call_args.kwargs["json"],
            {"model": "gpt-5.5", "input": "Say OK"},
        )
        self.assertNotIn(secret, response.text)

    def test_openai_400_body_is_parsed_and_sanitized(self) -> None:
        secret = "sk-testsecret123456789"
        client = self._client_without_keys()
        client.post(
            "/prompt-assistant/providers/connect",
            json={"provider": "openai", "api_key": secret},
        )

        with self.assertLogs("app.prompt_assistant", level="WARNING") as logs, patch(
            "app.prompt_assistant.httpx.post",
            return_value=self._provider_response(
                {
                    "error": {
                        "message": f"Bad request for api_key={secret}",
                        "type": "invalid_request_error",
                        "code": "bad_payload",
                    }
                },
                status_code=400,
            ),
        ):
            response = client.post(
                "/prompt-assistant/providers/test",
                json={"provider": "openai"},
            )

        self.assertEqual(response.status_code, 200)
        error = response.json()["error"]
        self.assertIn("status_code=400", error)
        self.assertIn("message=Bad request for api_key=[REDACTED]", error)
        self.assertIn("type=invalid_request_error", error)
        self.assertIn("code=bad_payload", error)
        self.assertNotIn(secret, response.text)
        self.assertNotIn(secret, "\n".join(logs.output))

    def test_openai_invalid_model_returns_actionable_message_without_verifying(self) -> None:
        secret = "sk-testsecret123456789"
        client = self._client_without_keys()
        client.post(
            "/prompt-assistant/providers/connect",
            json={"provider": "openai", "api_key": secret},
        )

        with self.assertLogs("app.prompt_assistant", level="WARNING"), patch(
            "app.prompt_assistant.httpx.post",
            return_value=self._provider_response(
                {
                    "error": {
                        "message": "The model gpt-5.5 does not exist or you do not have access.",
                        "type": "invalid_request_error",
                        "code": "model_not_found",
                    }
                },
                status_code=400,
            ),
        ):
            response = client.post(
                "/prompt-assistant/providers/test",
                json={"provider": "openai"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["last_verified_at"])
        self.assertEqual(
            payload["error"],
            "OpenAI key is valid, but model is not available. "
            "Check PROMPT_ASSISTANT_OPENAI_MODEL.",
        )
        self.assertNotIn(secret, response.text)

    def test_response_shape_is_stable(self) -> None:
        response = self._client_without_keys().post(
            "/prompt-assistant/refine",
            json={"provider": "fallback", "rough_idea": "fix flaky login test"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()),
            {
                "provider",
                "title",
                "recommended_scope",
                "refined_prompt",
                "safety_notes",
                "used_fallback",
            },
        )

    def test_no_api_key_appears_in_response_or_log_safe_errors(self) -> None:
        secret = "sk-testsecret123456789"
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir), openai_api_key=secret)
            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                with self.assertLogs("app.prompt_assistant", level="WARNING") as logs, patch(
                    "app.prompt_assistant.httpx.post",
                    side_effect=RuntimeError(f"provider failed api_key={secret}"),
                ):
                    response = client.post(
                        "/prompt-assistant/refine",
                        json={
                            "provider": "openai",
                            "rough_idea": "add export button",
                        },
                    )
            finally:
                app.dependency_overrides.clear()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(secret, response.text)
        self.assertNotIn(secret, "\n".join(logs.output))

    def test_raw_keys_redacted_from_errors_and_log_safe_text(self) -> None:
        openai_secret = "sk-testsecret123456789"
        anthropic_secret = "sk-ant-testsecret123456789"
        message = (
            f"authorization: bearer {openai_secret} "
            f"api_key={anthropic_secret} access_token={openai_secret}"
        )

        from app.prompt_assistant import redact_secret_text

        redacted = redact_secret_text(message)

        self.assertNotIn(openai_secret, redacted)
        self.assertNotIn(anthropic_secret, redacted)
        self.assertIn("[REDACTED]", redacted)

    def _client_without_keys(self) -> TestClient:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        settings = self._settings(Path(temp_dir.name))
        app.dependency_overrides[get_settings] = lambda: settings
        self.addCleanup(app.dependency_overrides.clear)
        return TestClient(app)

    def _settings(
        self,
        temp_path: Path,
        openai_api_key: str | None = None,
        anthropic_api_key: str | None = None,
    ) -> Settings:
        return Settings(
            WORKSPACES_DIR=temp_path / "workspaces",
            LOGS_DIR=temp_path / "logs",
            AGENTDOCK_DB_PATH=temp_path / "data" / "agentdock.sqlite3",
            OPENAI_API_KEY=openai_api_key,
            ANTHROPIC_API_KEY=anthropic_api_key,
        )

    def _provider_payload(self, prompt: str) -> str:
        return (
            '{"title":"Generated","recommended_scope":"related_files",'
            f'"refined_prompt":"{prompt}. Do not modify unrelated files.",'
            '"safety_notes":["Codex will not run until launch."]}'
        )

    def _provider_response(self, payload: dict[str, object], status_code: int = 200):
        class Response:
            def __init__(self) -> None:
                self.status_code = status_code

            @property
            def text(self) -> str:
                return str(payload)

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return payload

        return Response()
