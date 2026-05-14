from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
                codex_enabled=True,
            )

            with patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"):
                statuses = {
                    provider.id: provider for provider in provider_statuses(settings)
                }

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

            with patch("app.agent_providers.shutil.which", return_value=None):
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
            self.assertEqual(
                statuses["codex"]["required_config"],
                ["CODEX_ENABLED", "CODEX_CLI_COMMAND"],
            )

    def test_real_providers_start_disconnected_without_stored_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                openai_api_key="openai-key",
                anthropic_api_key="anthropic-key",
                ollama_base_url="http://127.0.0.1:11434",
                local_runner_command="agentdock-local",
                codex_enabled=True,
            )

            with patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"):
                providers = {
                    provider.id: provider for provider in provider_metadata(settings)
                }

            self.assertEqual(providers["codex"].status.value, "disconnected")
            self.assertEqual(providers["codex"].connection.value, "disconnected")
            self.assertEqual(providers["codex"].readiness.value, "ready")
            self.assertEqual(providers["claude"].status.value, "disconnected")
            self.assertEqual(providers["claude"].readiness.value, "unavailable")
            self.assertEqual(providers["ollama"].status.value, "disconnected")
            self.assertEqual(providers["ollama"].readiness.value, "ready")
            self.assertEqual(providers["local_model"].status.value, "disconnected")
            self.assertEqual(providers["local_model"].readiness.value, "ready")

    def test_codex_readiness_does_not_equal_connected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                openai_api_key="openai-key",
                codex_enabled=True,
            )

            with patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"):
                providers = {
                    provider.id: provider for provider in provider_metadata(settings)
                }

            self.assertEqual(providers["codex"].status.value, "disconnected")
            self.assertEqual(providers["codex"].connection.value, "disconnected")
            self.assertEqual(providers["codex"].readiness.value, "ready")
            self.assertEqual(
                providers["codex"].description,
                "Use local Codex CLI with your ChatGPT/OpenAI login.",
            )

    def test_codex_provider_connection_persists_connected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                openai_api_key="openai-key",
                codex_enabled=True,
            )
            AgentProviderConnectionStore(settings).connect("codex", "codex_cli")

            with patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"):
                providers = {
                    provider.id: provider for provider in provider_metadata(settings)
                }

            self.assertEqual(providers["codex"].status.value, "connected")
            self.assertEqual(providers["codex"].connection.value, "connected")
            self.assertEqual(providers["codex"].readiness.value, "ready")
            self.assertEqual(providers["codex"].auth_mode, "codex_cli")

    def test_codex_provider_disconnect_persists_disconnected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                openai_api_key="openai-key",
                codex_enabled=True,
            )
            store = AgentProviderConnectionStore(settings)
            store.connect("codex", "codex_cli")
            store.disconnect("codex")

            with patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"):
                providers = {
                    provider.id: provider for provider in provider_metadata(settings)
                }

            self.assertEqual(providers["codex"].status.value, "disconnected")

    def test_local_model_starts_disconnected_when_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                local_runner_command="agentdock-local",
            )

            providers = {provider.id: provider for provider in provider_metadata(settings)}

            self.assertEqual(providers["local_model"].connection.value, "disconnected")
            self.assertEqual(providers["local_model"].readiness.value, "ready")

    def test_connect_only_connects_selected_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                ollama_base_url="http://127.0.0.1:11434",
                local_runner_command="agentdock-local",
            )

            AgentProviderConnectionStore(settings).connect(
                "local_model",
                "local_runner",
            )
            providers = {provider.id: provider for provider in provider_metadata(settings)}

            self.assertEqual(providers["local_model"].connection.value, "connected")
            self.assertEqual(providers["ollama"].connection.value, "disconnected")
            self.assertEqual(providers["codex"].connection.value, "disconnected")
            self.assertEqual(providers["claude"].connection.value, "disconnected")

    def test_disconnect_only_disconnects_selected_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                ollama_base_url="http://127.0.0.1:11434",
                local_runner_command="agentdock-local",
            )
            store = AgentProviderConnectionStore(settings)
            store.connect("local_model", "local_runner")
            store.connect("ollama", "local_url")
            store.disconnect("local_model")

            providers = {provider.id: provider for provider in provider_metadata(settings)}

            self.assertEqual(providers["local_model"].connection.value, "disconnected")
            self.assertEqual(providers["ollama"].connection.value, "connected")

    def test_development_fallback_is_not_persisted_as_connected_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))

            connected = AgentProviderConnectionStore(settings).connected_auth_modes()
            providers = provider_metadata(settings)

            self.assertNotIn("development", connected)
            self.assertNotIn("development", {provider.id for provider in providers})

    def test_provider_connection_state_persists_in_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))

            store = AgentProviderConnectionStore(settings)
            store.connect("claude", "api_key")

            providers = {provider.id: provider for provider in provider_metadata(settings)}

            self.assertEqual(providers["claude"].status.value, "connected")
            self.assertEqual(providers["claude"].auth_mode, "api_key")

            restarted_store = AgentProviderConnectionStore(settings)
            restarted_store.disconnect("claude")

            disconnected = {
                provider.id: provider for provider in provider_metadata(settings)
            }
            self.assertEqual(disconnected["claude"].status.value, "disconnected")

    def test_provider_connect_and_disconnect_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                local_runner_command="agentdock-local",
            )

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                list_response = client.get("/agent-providers")
                connect_response = client.post(
                    "/agent-providers/connect",
                    json={"provider_id": "local_model", "auth_mode": "local_runner"},
                )
                disconnect_response = client.post(
                    "/agent-providers/disconnect",
                    json={"provider_id": "local_model"},
                )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(list_response.status_code, 200)
            providers = {provider["id"]: provider for provider in list_response.json()}
            self.assertEqual(providers["codex"]["label"], "OpenAI Codex")
            self.assertFalse(providers["codex"]["supports_chatgpt_login"])
            self.assertFalse(providers["codex"]["supports_api_key"])

            self.assertEqual(connect_response.status_code, 200)
            self.assertEqual(connect_response.json()["status"], "connected")
            self.assertEqual(connect_response.json()["connection"], "connected")
            self.assertEqual(connect_response.json()["readiness"], "ready")
            self.assertEqual(connect_response.json()["auth_mode"], "local_runner")

            self.assertEqual(disconnect_response.status_code, 200)
            self.assertEqual(disconnect_response.json()["status"], "disconnected")

    def _settings(
        self,
        temp_path: Path,
        openai_api_key: str | None = None,
        anthropic_api_key: str | None = None,
        ollama_base_url: str | None = None,
        local_runner_command: str | None = None,
        codex_enabled: bool = False,
    ) -> Settings:
        return Settings(
            WORKSPACES_DIR=temp_path / "workspaces",
            LOGS_DIR=temp_path / "logs",
            AGENTDOCK_DB_PATH=temp_path / "data" / "agentdock.sqlite3",
            OPENAI_API_KEY=openai_api_key,
            ANTHROPIC_API_KEY=anthropic_api_key,
            OLLAMA_BASE_URL=ollama_base_url,
            LOCAL_RUNNER_COMMAND=local_runner_command,
            CODEX_ENABLED=codex_enabled,
        )


if __name__ == "__main__":
    unittest.main()
