from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.agent_providers import (
    AgentProviderConnectionStore,
    provider_metadata,
    provider_statuses,
)
from app.config import Settings, get_settings
from app.main import app
from app.models import AgentProviderStatus


class AgentProviderStatusTests(unittest.TestCase):
    def test_provider_statuses_reflect_basic_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                openai_api_key="openai-key",
                anthropic_api_key=None,
                ollama_base_url=None,
                local_runner_command=None,
            )

            statuses = {provider.id: provider for provider in provider_statuses(settings)}

            self.assertEqual(statuses["codex"].status, AgentProviderStatus.available)
            self.assertEqual(
                statuses["claude"].status,
                AgentProviderStatus.not_configured,
            )
            self.assertEqual(
                statuses["ollama"].status,
                AgentProviderStatus.local_required,
            )
            self.assertEqual(
                statuses["local_model"].status,
                AgentProviderStatus.local_required,
            )
            self.assertEqual(
                statuses["development"].status,
                AgentProviderStatus.available,
            )
            self.assertEqual(statuses["development"].label, "Development agent")
            self.assertEqual(statuses["claude"].required_config, ["ANTHROPIC_API_KEY"])

    def test_provider_status_endpoint_returns_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                openai_api_key=None,
                anthropic_api_key="anthropic-key",
                ollama_base_url="http://127.0.0.1:11434",
                local_runner_command="agentdock-local",
            )

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                response = client.get("/agent-providers/status")
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            statuses = {provider["id"]: provider for provider in response.json()}
            self.assertEqual(statuses["codex"]["status"], "not_configured")
            self.assertEqual(statuses["claude"]["status"], "available")
            self.assertEqual(statuses["ollama"]["status"], "available")
            self.assertEqual(statuses["local_model"]["status"], "available")
            self.assertEqual(statuses["codex"]["required_config"], ["OPENAI_API_KEY"])

    def test_provider_connection_state_persists_in_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))

            store = AgentProviderConnectionStore(settings)
            store.connect("codex", "chatgpt")

            providers = {provider.id: provider for provider in provider_metadata(settings)}

            self.assertEqual(providers["codex"].status.value, "connected")
            self.assertEqual(providers["codex"].auth_mode, "chatgpt")

            restarted_store = AgentProviderConnectionStore(settings)
            restarted_store.disconnect("codex")

            disconnected = {
                provider.id: provider for provider in provider_metadata(settings)
            }
            self.assertEqual(disconnected["codex"].status.value, "disconnected")

    def test_provider_connect_and_disconnect_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                list_response = client.get("/agent-providers")
                connect_response = client.post(
                    "/agent-providers/connect",
                    json={"provider_id": "codex", "auth_mode": "chatgpt"},
                )
                disconnect_response = client.post(
                    "/agent-providers/disconnect",
                    json={"provider_id": "codex"},
                )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(list_response.status_code, 200)
            providers = {provider["id"]: provider for provider in list_response.json()}
            self.assertEqual(providers["codex"]["label"], "OpenAI Codex")
            self.assertTrue(providers["codex"]["supports_chatgpt_login"])
            self.assertTrue(providers["codex"]["supports_api_key"])

            self.assertEqual(connect_response.status_code, 200)
            self.assertEqual(connect_response.json()["status"], "connected")
            self.assertEqual(connect_response.json()["auth_mode"], "chatgpt")

            self.assertEqual(disconnect_response.status_code, 200)
            self.assertEqual(disconnect_response.json()["status"], "disconnected")

    def _settings(
        self,
        temp_path: Path,
        openai_api_key: str | None = None,
        anthropic_api_key: str | None = None,
        ollama_base_url: str | None = None,
        local_runner_command: str | None = None,
    ) -> Settings:
        return Settings(
            WORKSPACES_DIR=temp_path / "workspaces",
            LOGS_DIR=temp_path / "logs",
            AGENTDOCK_DB_PATH=temp_path / "data" / "agentdock.sqlite3",
            OPENAI_API_KEY=openai_api_key,
            ANTHROPIC_API_KEY=anthropic_api_key,
            OLLAMA_BASE_URL=ollama_base_url,
            LOCAL_RUNNER_COMMAND=local_runner_command,
        )


if __name__ == "__main__":
    unittest.main()
