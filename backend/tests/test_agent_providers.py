from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import app.agent_providers as agent_providers
from app.agent_providers import (
    CODEX_ACTIVE_AUTH_MODE,
    CODEX_API_KEY_AUTH_MODE,
    CODEX_CHATGPT_OAUTH_AUTH_MODE,
    AgentProviderConnectionStore,
    AgentProviderError,
    connect_provider,
    parse_codex_device_auth_output,
    provider_metadata,
    provider_statuses,
    verify_provider_connection,
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
            self.assertEqual(providers["codex"].readiness.value, "auth_required")
            self.assertEqual(providers["codex"].primary_status.value, "disconnected")
            self.assertEqual(providers["claude"].status.value, "disconnected")
            self.assertEqual(providers["claude"].readiness.value, "unavailable")
            self.assertEqual(providers["ollama"].status.value, "disconnected")
            self.assertEqual(providers["ollama"].readiness.value, "ready")
            self.assertEqual(providers["ollama"].primary_status.value, "disconnected")
            self.assertEqual(providers["local_model"].status.value, "disconnected")
            self.assertEqual(providers["local_model"].readiness.value, "ready")
            self.assertEqual(providers["local_model"].primary_status.value, "disconnected")

    def test_codex_readiness_does_not_equal_connected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                openai_api_key=None,
                codex_enabled=True,
            )

            with (
                patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"),
                patch("subprocess.run") as run,
            ):
                providers = {
                    provider.id: provider for provider in provider_metadata(settings)
                }

            run.assert_not_called()
            self.assertEqual(providers["codex"].status.value, "disconnected")
            self.assertEqual(providers["codex"].connection.value, "disconnected")
            self.assertEqual(providers["codex"].readiness.value, "auth_required")
            self.assertEqual(providers["codex"].primary_status.value, "disconnected")
            self.assertEqual(
                providers["codex"].description,
                "Connect with ChatGPT or an OpenAI API key. Cloud execution is not enabled yet.",
            )

    def test_codex_provider_activation_persists_as_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                openai_api_key="openai-key",
                codex_enabled=True,
            )
            AgentProviderConnectionStore(settings).connect(
                "codex",
                CODEX_ACTIVE_AUTH_MODE,
            )

            with patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"):
                providers = {
                    provider.id: provider for provider in provider_metadata(settings)
                }

            self.assertEqual(providers["codex"].status.value, "connected")
            self.assertEqual(providers["codex"].connection.value, "connected")
            self.assertEqual(providers["codex"].readiness.value, "ready")
            self.assertEqual(providers["codex"].primary_status.value, "active")
            self.assertEqual(providers["codex"].auth_mode, CODEX_ACTIVE_AUTH_MODE)

    def test_main_codex_connect_starts_device_auth(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                openai_api_key=None,
                codex_enabled=True,
            )

            with (
                patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"),
                patch(
                    "app.agent_providers._start_codex_device_auth",
                    return_value={
                        "verification_url": "https://auth.openai.com/codex/device",
                        "user_code": "ABCD-1234",
                        "device_code": "device-secret",
                    },
                ) as start_device_auth,
            ):
                flow = connect_provider(
                    settings,
                    "codex",
                    auth_mode=CODEX_CHATGPT_OAUTH_AUTH_MODE,
                )

            self.assertEqual(flow.provider_id, "codex")
            self.assertEqual(flow.status, "waiting_for_auth")
            self.assertIsNone(flow.command)
            self.assertEqual(flow.auth_url, "https://auth.openai.com/codex/device")
            self.assertEqual(flow.verification_url, "https://auth.openai.com/codex/device")
            self.assertEqual(flow.device_code, "device-secret")
            self.assertEqual(flow.user_code, "ABCD-1234")
            self.assertEqual(
                flow.message,
                "Open the Codex verification link, sign in with your ChatGPT account, then return to AisoFlow and tap Check connection.",
            )
            self.assertEqual(
                flow.instructions,
                [
                    "Open the verification link on this device or another browser.",
                    "Sign in with your ChatGPT account and enter the displayed code if prompted.",
                    "Return to AisoFlow and tap Check connection.",
                ],
            )
            start_device_auth.assert_called_once_with(settings)
            self.assertNotIn("codex", AgentProviderConnectionStore(settings).connected_auth_modes())
            self.assertEqual(flow.metadata.connection.value, "disconnected")

    def test_codex_device_auth_output_parsing(self) -> None:
        parsed = parse_codex_device_auth_output(
            "Visit: https://auth.openai.com/device\n"
            "Enter code: ABCD-1234\n"
            "device code: device-secret"
        )

        self.assertEqual(parsed["verification_url"], "https://auth.openai.com/device")
        self.assertEqual(parsed["user_code"], "ABCD-1234")
        self.assertEqual(parsed["device_code"], "device-secret")

    def test_codex_device_auth_parser_supports_open_and_https_fallback(self) -> None:
        opened = parse_codex_device_auth_output(
            "Open: https://auth.openai.com/device\nUser code: WXYZ-5678"
        )
        fallback = parse_codex_device_auth_output(
            "Continue at https://auth.openai.com/device\nEnter code: EFGH-9012"
        )
        localhost = parse_codex_device_auth_output(
            "Signed in at http://localhost:1455/success?state=ok"
        )

        self.assertEqual(opened["verification_url"], "https://auth.openai.com/device")
        self.assertEqual(opened["user_code"], "WXYZ-5678")
        self.assertEqual(fallback["verification_url"], "https://auth.openai.com/device")
        self.assertEqual(fallback["user_code"], "EFGH-9012")
        self.assertNotIn("verification_url", localhost)

    def test_codex_device_auth_parser_strips_ansi_escape_sequences(self) -> None:
        parsed = parse_codex_device_auth_output(
            "\x1b[32mVisit: https://auth.openai.com/codex/device\x1b[0m\n"
            "\x1b[?25hEnter code: ABCD-1234\n"
            "device code: device-secret\x1b[0m"
        )

        self.assertEqual(
            parsed["verification_url"],
            "https://auth.openai.com/codex/device",
        )
        self.assertEqual(parsed["user_code"], "ABCD-1234")
        self.assertEqual(parsed["device_code"], "device-secret")
        self.assertNotRegex(parsed["verification_url"], r"\x1b\[[0-?]*[ -/]*[@-~]")

    def test_codex_account_flow_starts_device_auth_login_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir), codex_enabled=True)
            process = Mock()
            process.poll.return_value = None
            process.stdout = None

            with (
                patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"),
                patch("app.agent_providers.subprocess.Popen", return_value=process) as popen,
                patch(
                    "app.agent_providers._read_initial_process_output",
                    return_value=[
                        "Visit: https://auth.openai.com/codex/device\x1b[0m",
                        "Enter code: ABCD-1234",
                    ],
                ),
            ):
                flow = agent_providers._start_codex_device_auth(settings)

            self.assertEqual(
                flow["verification_url"],
                "https://auth.openai.com/codex/device",
            )
            self.assertEqual(flow["user_code"], "ABCD-1234")
            self.assertEqual(popen.call_args.args[0], ["codex", "login", "--device-auth"])

    def test_codex_device_auth_reader_collects_url_and_code_from_separate_lines(self) -> None:
        process = Mock()
        process.poll.return_value = None
        process.stdout = iter(
            [
                "Visit: https://auth.openai.com/device\n",
                "Enter code: ABCD-1234\n",
                "Not needed after code\n",
            ]
        )

        lines = agent_providers._read_initial_process_output(process, timeout_seconds=1)

        self.assertEqual(
            lines,
            ["Visit: https://auth.openai.com/device", "Enter code: ABCD-1234"],
        )

    def test_codex_device_auth_parse_failure_reports_redacted_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir), codex_enabled=True)
            process = Mock()
            process.poll.return_value = None
            process.stdout = None

            with (
                patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"),
                patch("app.agent_providers.subprocess.Popen", return_value=process),
                patch(
                    "app.agent_providers._read_initial_process_output",
                    return_value=["Authentication error: token=secret-token"],
                ),
                self.assertLogs("app.agent_providers", level="WARNING") as logs,
            ):
                with self.assertRaises(AgentProviderError) as error:
                    agent_providers._start_codex_device_auth(settings)

            self.assertIn("[REDACTED]", str(error.exception))
            self.assertNotIn("secret-token", str(error.exception))
            self.assertIn("[REDACTED]", "\n".join(logs.output))
            self.assertNotIn("secret-token", "\n".join(logs.output))

    def test_successful_codex_verification_marks_connected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                openai_api_key=None,
                codex_enabled=True,
            )
            completed = subprocess.CompletedProcess(
                args=["codex"],
                returncode=0,
                stdout="OK\n",
                stderr="",
            )

            with (
                patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"),
                patch("app.agent_providers.subprocess.run", return_value=completed) as run,
            ):
                result = verify_provider_connection(
                    settings,
                    "codex",
                    auth_mode=CODEX_CHATGPT_OAUTH_AUTH_MODE,
                )

            args = run.call_args.args[0]
            self.assertEqual(args, ["codex", "login", "status"])
            self.assertNotIn("--ask-for-approval", args)
            self.assertTrue(result.connected)
            self.assertEqual(result.message, "Authorization detected.")
            self.assertEqual(result.metadata.connection.value, "connected")
            self.assertEqual(result.metadata.primary_status.value, "active")
            self.assertEqual(
                AgentProviderConnectionStore(settings).connected_auth_modes()["codex"],
                CODEX_CHATGPT_OAUTH_AUTH_MODE,
            )

    def test_failed_codex_verification_remains_disconnected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                openai_api_key=None,
                codex_enabled=True,
            )
            completed = subprocess.CompletedProcess(
                args=["codex"],
                returncode=1,
                stdout="",
                stderr="not logged in",
            )

            with (
                patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"),
                patch("app.agent_providers.subprocess.run", return_value=completed),
            ):
                result = verify_provider_connection(
                    settings,
                    "codex",
                    auth_mode=CODEX_CHATGPT_OAUTH_AUTH_MODE,
                )

            self.assertFalse(result.connected)
            self.assertIn("verification failed", result.message)
            self.assertNotIn("codex", AgentProviderConnectionStore(settings).connected_auth_modes())
            self.assertEqual(result.metadata.connection.value, "disconnected")

    def test_codex_verify_falls_back_to_exec_when_status_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                openai_api_key=None,
                codex_enabled=True,
            )

            unsupported = subprocess.CompletedProcess(
                args=["codex"],
                returncode=2,
                stdout="",
                stderr="unknown command status",
            )
            ok = subprocess.CompletedProcess(
                args=["codex"],
                returncode=0,
                stdout="OK\n",
                stderr="",
            )

            with (
                patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"),
                patch("app.agent_providers.subprocess.run", side_effect=[unsupported, ok]) as run,
            ):
                result = verify_provider_connection(settings, "codex")

            self.assertEqual(run.call_args_list[0].args[0], ["codex", "login", "status"])
            self.assertEqual(run.call_args_list[1].args[0], ["codex", "exec", "Say OK"])
            self.assertTrue(result.connected)

    def test_codex_verification_stores_no_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                openai_api_key=None,
                codex_enabled=True,
            )
            completed = subprocess.CompletedProcess(
                args=["codex"],
                returncode=0,
                stdout="OK\n",
                stderr="",
            )

            with (
                patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"),
                patch("app.agent_providers.subprocess.run", return_value=completed),
            ):
                verify_provider_connection(
                    settings,
                    "codex",
                    auth_mode=CODEX_CHATGPT_OAUTH_AUTH_MODE,
                )

            self.assertEqual(
                AgentProviderConnectionStore(settings).connected_auth_modes(),
                {"codex": CODEX_CHATGPT_OAUTH_AUTH_MODE},
            )

    def test_codex_provider_stop_using_persists_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                openai_api_key="openai-key",
                codex_enabled=True,
            )
            store = AgentProviderConnectionStore(settings)
            store.connect("codex", CODEX_ACTIVE_AUTH_MODE)
            store.disconnect("codex")

            with patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"):
                providers = {
                    provider.id: provider for provider in provider_metadata(settings)
                }

            self.assertEqual(providers["codex"].status.value, "disconnected")
            self.assertEqual(providers["codex"].primary_status.value, "disconnected")

    def test_old_codex_simulated_connection_is_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                openai_api_key="openai-key",
                codex_enabled=True,
            )
            store = AgentProviderConnectionStore(settings)
            store.connect("codex", "codex_cli")

            with patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"):
                providers = {
                    provider.id: provider for provider in provider_metadata(settings)
                }

            self.assertNotIn(
                "codex",
                AgentProviderConnectionStore(settings).connected_auth_modes(),
            )
            self.assertEqual(providers["codex"].connection.value, "disconnected")
            self.assertEqual(providers["codex"].primary_status.value, "disconnected")

    def test_codex_disabled_still_allows_product_auth_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                openai_api_key="openai-key",
                codex_enabled=False,
            )

            with patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"):
                providers = {
                    provider.id: provider for provider in provider_metadata(settings)
                }

            self.assertEqual(providers["codex"].readiness.value, "auth_required")
            self.assertEqual(providers["codex"].primary_status.value, "disconnected")

    def test_codex_missing_cli_still_allows_product_auth_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                openai_api_key="openai-key",
                codex_enabled=True,
            )

            with patch("app.agent_providers.shutil.which", return_value=None):
                providers = {
                    provider.id: provider for provider in provider_metadata(settings)
                }

            self.assertEqual(providers["codex"].readiness.value, "auth_required")
            self.assertEqual(providers["codex"].primary_status.value, "disconnected")

    def test_codex_readiness_does_not_call_unsupported_auth_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                openai_api_key=None,
                codex_enabled=True,
            )

            with (
                patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"),
                patch("subprocess.run") as run,
            ):
                providers = {
                    provider.id: provider for provider in provider_metadata(settings)
                }

            run.assert_not_called()
            self.assertEqual(providers["codex"].readiness.value, "auth_required")
            self.assertEqual(providers["codex"].primary_status.value, "disconnected")

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
            self.assertTrue(providers["codex"]["supports_chatgpt_login"])
            self.assertTrue(providers["codex"]["supports_api_key"])

            self.assertEqual(connect_response.status_code, 200)
            self.assertEqual(connect_response.json()["status"], "connected")
            self.assertEqual(connect_response.json()["connection"], "connected")
            self.assertEqual(connect_response.json()["readiness"], "ready")
            self.assertEqual(connect_response.json()["auth_mode"], "local_runner")

            self.assertEqual(disconnect_response.status_code, 200)
            self.assertEqual(disconnect_response.json()["status"], "disconnected")

    def test_codex_connect_endpoint_returns_device_auth_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                codex_enabled=True,
            )

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                with (
                    patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"),
                    patch(
                        "app.agent_providers._start_codex_device_auth",
                        return_value={
                            "verification_url": "https://auth.openai.com/codex/device",
                            "user_code": "ABCD-1234",
                            "device_code": "device-secret",
                        },
                    ) as start_device_auth,
                ):
                    response = client.post(
                        "/agent-providers/connect",
                        json={
                            "provider_id": "codex",
                            "auth_mode": CODEX_CHATGPT_OAUTH_AUTH_MODE,
                        },
                    )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "waiting_for_auth")
            self.assertIsNone(payload["command"])
            self.assertEqual(
                payload["message"],
                "Open the Codex verification link, sign in with your ChatGPT account, then return to AisoFlow and tap Check connection.",
            )
            self.assertEqual(payload["auth_url"], "https://auth.openai.com/codex/device")
            self.assertEqual(payload["verification_url"], "https://auth.openai.com/codex/device")
            self.assertEqual(payload["device_code"], "device-secret")
            self.assertEqual(payload["user_code"], "ABCD-1234")
            self.assertEqual(
                payload["instructions"],
                [
                    "Open the verification link on this device or another browser.",
                    "Sign in with your ChatGPT account and enter the displayed code if prompted.",
                    "Return to AisoFlow and tap Check connection.",
                ],
            )
            self.assertEqual(payload["metadata"]["connection"], "disconnected")
            start_device_auth.assert_called_once_with(settings)
            self.assertNotIn(
                "codex",
                AgentProviderConnectionStore(settings).connected_auth_modes(),
            )

    def test_codex_verify_endpoint_marks_connected_after_smoke_test(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                codex_enabled=True,
            )
            completed = subprocess.CompletedProcess(
                args=["codex"],
                returncode=0,
                stdout="OK\n",
                stderr="",
            )

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                with (
                    patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"),
                    patch("app.agent_providers.subprocess.run", return_value=completed),
                ):
                    response = client.post(
                        "/agent-providers/verify",
                        json={
                            "provider_id": "codex",
                            "auth_mode": CODEX_CHATGPT_OAUTH_AUTH_MODE,
                        },
                    )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["connected"])
            self.assertEqual(payload["metadata"]["connection"], "connected")

    def test_codex_verify_endpoint_failure_stays_disconnected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(
                Path(temp_dir),
                codex_enabled=True,
            )
            completed = subprocess.CompletedProcess(
                args=["codex"],
                returncode=1,
                stdout="",
                stderr="not logged in",
            )

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                with (
                    patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"),
                    patch("app.agent_providers.subprocess.run", return_value=completed),
                ):
                    response = client.post(
                        "/agent-providers/verify",
                        json={
                            "provider_id": "codex",
                            "auth_mode": CODEX_CHATGPT_OAUTH_AUTH_MODE,
                        },
                    )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 400)
            self.assertNotIn(
                "codex",
                AgentProviderConnectionStore(settings).connected_auth_modes(),
            )

    def test_codex_api_key_submit_stores_auth_server_side(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir), codex_enabled=True)
            login_ok = subprocess.CompletedProcess(
                args=["codex"],
                returncode=0,
                stdout="",
                stderr="",
            )
            status_ok = subprocess.CompletedProcess(
                args=["codex"],
                returncode=0,
                stdout="OK\n",
                stderr="",
            )

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                with (
                    patch("app.agent_providers.shutil.which", return_value="/usr/bin/codex"),
                    patch(
                        "app.agent_providers.subprocess.run",
                        side_effect=[login_ok, status_ok],
                    ) as run,
                ):
                    response = client.post(
                        "/agent-providers/api-key",
                        json={
                            "provider_id": "codex",
                            "api_key": "sk-test-agentdock-secret-value",
                        },
                    )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(response.status_code, 200)
            self.assertEqual(run.call_args_list[0].kwargs["input"], "sk-test-agentdock-secret-value\n")
            payload = response.json()
            self.assertEqual(payload["connection"], "connected")
            self.assertEqual(payload["auth_mode"], "api_key")
            self.assertEqual(
                AgentProviderConnectionStore(settings).connected_auth_modes(),
                {"codex": "api_key"},
            )
            self.assertNotIn("sk-test-agentdock-secret-value", response.text)
            with AgentProviderConnectionStore(settings)._connect() as connection:
                columns = {
                    str(row["name"])
                    for row in connection.execute(
                        "PRAGMA table_info(agent_provider_credentials)"
                    ).fetchall()
                }
            self.assertNotIn("secret_value", columns)

    def test_removed_custom_codex_oauth_routes_return_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._settings(Path(temp_dir))

            app.dependency_overrides[get_settings] = lambda: settings
            try:
                client = TestClient(app)
                start_response = client.get("/agent-providers/codex/oauth/start")
                callback_response = client.get(
                    "/agent-providers/codex/oauth/callback",
                    params={"code": "auth-code", "state": "state"},
                )
            finally:
                app.dependency_overrides.clear()

            self.assertEqual(start_response.status_code, 404)
            self.assertEqual(callback_response.status_code, 404)
            self.assertNotIn(
                "codex",
                AgentProviderConnectionStore(settings).connected_auth_modes(),
            )

    def test_primary_codex_ui_contains_no_cli_commands(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        web_app = (repo_root / "frontend" / "src" / "App.tsx").read_text()
        mobile_app = (repo_root / "mobile" / "App.tsx").read_text()

        for source in (web_app, mobile_app):
            self.assertNotIn("More options", source)
            self.assertNotIn("codex login", source)
            self.assertNotIn("device-auth", source)
            self.assertNotIn("local Codex CLI", source)
            self.assertNotIn("Advanced: use local Codex CLI", source)

    def test_frontend_does_not_persist_raw_codex_api_key(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        web_app = (repo_root / "frontend" / "src" / "App.tsx").read_text()
        mobile_app = (repo_root / "mobile" / "App.tsx").read_text()

        self.assertNotIn("localStorage.setItem('codex", web_app)
        self.assertNotIn('localStorage.setItem("codex', web_app)
        self.assertNotIn("AsyncStorage", mobile_app)

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
