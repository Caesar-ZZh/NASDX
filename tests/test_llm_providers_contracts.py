from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nasdx import llm as legacy_llm  # noqa: E402
from nasdx import llm_providers as providers  # noqa: E402


def _test_credential() -> str:
    return "-".join(("unit", "test", "credential"))


class _FakeCompletions:
    def __init__(self) -> None:
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="你"))]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="好"))]),
        ]


class _FakeClient:
    def __init__(self, **kwargs) -> None:
        self.init_kwargs = kwargs
        self.completions = _FakeCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


class LLMProviderContracts(unittest.TestCase):
    def test_api_preset_autofills_url_and_model(self):
        result = providers.resolve_provider_config(
            {"provider": "deepseek", "apiKey": _test_credential()}, environ={}
        )
        self.assertEqual("api", result.mode)
        self.assertEqual("https://api.deepseek.com", result.base_url)
        self.assertEqual("deepseek-chat", result.model)

    def test_request_configuration_does_not_mutate_environment(self):
        env = {"NASDX_API_KEY": _test_credential()}
        with mock.patch.dict("os.environ", env, clear=True):
            before = dict(__import__("os").environ)
            result = providers.resolve_provider_config(
                {"provider": "openai", "apiKey": _test_credential()}
            )
            self.assertEqual(_test_credential(), result.api_key)
            self.assertEqual(before, dict(__import__("os").environ))

    def test_documented_llm_environment_aliases_work(self):
        result = providers.resolve_provider_config(
            {"provider": "custom"},
            environ={
                "LLM_API_KEY": _test_credential(),
                "LLM_BASE_URL": "https://gateway.example/v1",
                "LLM_MODEL": "model-x",
            },
        )
        self.assertEqual("model-x", result.model)

    def test_ollama_needs_no_key(self):
        result = providers.resolve_provider_config({"provider": "ollama"}, environ={})
        self.assertEqual("", result.api_key)
        self.assertTrue(result.public_dict()["configured"])

    def test_missing_key_is_a_configuration_error_event(self):
        events = list(
            providers.stream_chat_events(
                {"provider": "deepseek"}, [{"role": "user", "content": "hello"}], environ={}
            )
        )
        self.assertEqual("error", events[0]["type"])
        self.assertEqual("configuration", events[0]["code"])

    def test_custom_provider_requires_url_and_model(self):
        with self.assertRaises(providers.ProviderConfigurationError):
            providers.resolve_provider_config(
                {"provider": "custom", "apiKey": _test_credential()}, environ={}
            )

    def test_unknown_provider_reports_supported_routes(self):
        with self.assertRaises(providers.ProviderConfigurationError):
            providers.resolve_provider_config({"provider": "unknown"}, environ={})

    def test_api_route_streams_openai_compatible_chunks(self):
        clients = []

        def factory(**kwargs):
            client = _FakeClient(**kwargs)
            clients.append(client)
            return client

        events = list(
            providers.stream_chat_events(
                {"provider": "qwen", "apiKey": _test_credential()},
                [{"role": "user", "content": "hello"}],
                environ={},
                client_factory=factory,
            )
        )
        self.assertEqual(["delta", "delta", "done"], [event["type"] for event in events])
        self.assertEqual("你好", "".join(event.get("text", "") for event in events))
        self.assertTrue(clients[0].completions.kwargs["stream"])
        self.assertEqual("https://dashscope.aliyuncs.com/compatible-mode/v1", clients[0].init_kwargs["base_url"])

    def test_runtime_error_does_not_echo_request_key(self):
        def failing_factory(**_kwargs):
            raise RuntimeError(f"Bearer {_test_credential()} exploded")

        events = list(
            providers.stream_chat_events(
                {"provider": "openai", "apiKey": _test_credential()},
                [{"role": "user", "content": "hello"}],
                environ={},
                client_factory=failing_factory,
            )
        )
        self.assertEqual("provider_runtime", events[0]["code"])
        self.assertNotIn(_test_credential(), json.dumps(events))

    def test_cli_route_uses_allowlisted_binary_without_shell(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, stdout="CLI answer", stderr="")

        events = list(
            providers.stream_chat_events(
                {"provider": "cli-codex"},
                [{"role": "user", "content": "hello"}],
                environ={},
                which=lambda name: "C:/Tools/codex.exe" if name == "codex" else None,
                cli_runner=runner,
            )
        )
        self.assertEqual("CLI answer", events[0]["text"])
        self.assertEqual("C:/Tools/codex.exe", calls[0][0][0])
        self.assertFalse(calls[0][1]["shell"])
        self.assertNotIn("hello", calls[0][0])

    def test_missing_cli_is_a_configuration_error(self):
        with self.assertRaises(providers.ProviderConfigurationError):
            providers.resolve_provider_config(
                {"provider": "cli-claude"}, environ={}, which=lambda _name: None
            )

    def test_mcp_route_returns_attachment_instead_of_calling_a_model(self):
        events = list(
            providers.stream_chat_events(
                {"provider": "mcp"}, [{"role": "user", "content": "hello"}], environ={}
            )
        )
        self.assertEqual("mcp", events[0]["mode"])
        server = events[0]["attachment"]["mcpServers"]["nasdx-llm"]
        self.assertNotIn("env", server)
        self.assertEqual(["-m", "nasdx.llm_providers", "--mcp"], server["args"])

    def test_mcp_lifecycle_and_tool_list(self):
        initialized = providers.handle_mcp_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "v-test"}}
        )
        listed = providers.handle_mcp_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual("v-test", initialized["result"]["protocolVersion"])
        self.assertEqual("llm_provider_status", listed["result"]["tools"][0]["name"])

    def test_public_diagnostics_never_include_api_key(self):
        resolved = providers.resolve_provider_config(
            {"provider": "openai", "apiKey": _test_credential()}, environ={}
        )
        self.assertNotIn("api_key", resolved.public_dict())
        self.assertNotIn(_test_credential(), json.dumps(resolved.public_dict()))

    def test_legacy_llm_exports_request_scoped_router(self):
        result = legacy_llm.resolve_request_provider(
            {"provider": "ollama"}, environ={}
        )
        self.assertEqual("ollama", result.provider)


if __name__ == "__main__":
    unittest.main()
