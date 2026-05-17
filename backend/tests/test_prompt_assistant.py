from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app


class PromptAssistantTests(unittest.TestCase):
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
