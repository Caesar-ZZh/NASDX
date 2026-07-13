import unittest
from types import SimpleNamespace
from unittest.mock import patch

from nasdx.agents.base import BaseAgent
from nasdx.agents.chokepoint import ChokepointAgent
from nasdx.agents.fund_flow import FundFlowAgent
from nasdx.agents.risk import RiskAgent
from nasdx.agents.sector import SectorAgent
from nasdx.agents.synthesis import SynthesisAgent
from nasdx.agents.technical import TechnicalAgent


class DummyAgent(BaseAgent):
    def _analyze(self, stock_code, stock_data):
        raise NotImplementedError


class LLMStructuredContractsTest(unittest.TestCase):
    @staticmethod
    def _client(module, side_effect, model="deepseek-chat"):
        calls = []

        class Completions:
            def create(self, **kwargs):
                calls.append(kwargs["model"])
                if callable(side_effect):
                    return side_effect(kwargs)
                raise side_effect

        client = object.__new__(module.LLMClient)
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        client.model = model
        client.max_tokens = 128
        client.temperature = 0.1
        return client, calls

    def test_remote_provider_without_key_fails_before_http_call(self):
        import nasdx.llm as llm_module

        class FailingCompletions:
            calls = 0

            def create(self, **_kwargs):
                self.calls += 1
                raise AssertionError("HTTP client must not be called without an API key")

        client = object.__new__(llm_module.LLMClient)
        completions = FailingCompletions()
        client.client = type("Client", (), {"chat": type("Chat", (), {"completions": completions})()})()
        client.model = "remote-model"
        client.max_tokens = 128
        client.temperature = 0.1

        with (
            patch.object(llm_module, "API_KEY", ""),
            patch.object(llm_module, "BASE_URL", "https://notlocalhost.example/v1"),
        ):
            with self.assertRaisesRegex(RuntimeError, "请先设置 NASDX_API_KEY"):
                client.ask([{"role": "user", "content": "hello"}], max_retries=1)

        self.assertEqual(completions.calls, 0)

    def test_extract_json_payload_from_fenced_model_response(self):
        from nasdx.llm import extract_json_payload

        text = """
        先给一点解释。

        ```json
        {
          "signal": "bullish",
          "confidence": 0.82,
          "conclusion": "趋势转强但仍需等待成交确认。",
          "key_points": ["MA5上穿MA20", "MACD翻红"]
        }
        ```
        """
        payload = extract_json_payload(text)

        self.assertEqual(payload["signal"], "bullish")
        self.assertEqual(payload["confidence"], 0.82)
        self.assertEqual(payload["key_points"], ["MA5上穿MA20", "MACD翻红"])

    def test_model_attempts_are_ordered_unique(self):
        import nasdx.llm as llm_module

        class ServiceUnavailable(Exception):
            status_code = 503

        client, calls = self._client(llm_module, ServiceUnavailable("service unavailable"))
        client.FALLBACK_MODELS = ["deepseek-chat", "deepseek-reasoner", "deepseek-chat", ""]
        with (
            patch.object(llm_module, "API_KEY", "configured"),
            patch.object(llm_module.time, "sleep", return_value=None),
        ):
            with self.assertRaises(RuntimeError):
                client.ask([{"role": "user", "content": "hello"}], max_retries=1, max_total_attempts=4)
        self.assertEqual(["deepseek-chat", "deepseek-reasoner"], calls)

    def test_invalid_request_fails_once_without_fallback(self):
        import nasdx.llm as llm_module

        class InvalidRequest(Exception):
            status_code = 400

        client, calls = self._client(llm_module, InvalidRequest("context_length_exceeded secret-prompt"))
        client.FALLBACK_MODELS = ["deepseek-reasoner"]
        with patch.object(llm_module, "API_KEY", "configured"):
            with self.assertRaisesRegex(RuntimeError, "请求无效") as caught:
                client.ask([{"role": "user", "content": "hello"}], max_retries=3)
        self.assertEqual(["deepseek-chat"], calls)
        self.assertNotIn("secret-prompt", str(caught.exception))

    def test_rate_limit_respects_retry_after_and_total_attempt_budget(self):
        import nasdx.llm as llm_module

        class RateLimited(Exception):
            status_code = 429

            def __init__(self):
                super().__init__("rate limited")
                self.response = SimpleNamespace(headers={"retry-after": "2"})

        client, calls = self._client(llm_module, RateLimited())
        client.FALLBACK_MODELS = ["deepseek-reasoner"]
        with (
            patch.object(llm_module, "API_KEY", "configured"),
            patch.object(llm_module.time, "sleep") as sleep,
            patch.object(llm_module.random, "uniform", return_value=0.0),
        ):
            with self.assertRaises(RuntimeError):
                client.ask(
                    [{"role": "user", "content": "hello"}],
                    max_retries=3,
                    max_total_attempts=2,
                    max_elapsed_seconds=30,
                )
        self.assertEqual(2, len(calls))
        sleep.assert_called_once_with(2.0)

    def test_authentication_failure_is_not_retried_or_leaked(self):
        import nasdx.llm as llm_module

        class AuthenticationFailed(Exception):
            status_code = 401

        client, calls = self._client(llm_module, AuthenticationFailed("credential-value-must-not-leak"))
        client.FALLBACK_MODELS = ["deepseek-reasoner"]
        with patch.object(llm_module, "API_KEY", "configured"):
            with self.assertRaises(RuntimeError) as caught:
                client.ask([{"role": "user", "content": "hello"}], max_retries=3)
        self.assertEqual(["deepseek-chat"], calls)
        self.assertNotIn("credential-value-must-not-leak", str(caught.exception))

    def test_non_deepseek_provider_has_no_implicit_deepseek_fallback(self):
        from nasdx.llm import _default_fallback_models

        self.assertEqual([], _default_fallback_models("https://apihub.agnes-ai.com/v1", "agnes-2.0-flash"))
        self.assertEqual(
            ["deepseek-reasoner", "deepseek-chat"],
            _default_fallback_models("https://api.deepseek.com", "deepseek-chat"),
        )

    def test_technical_agent_uses_structured_payload_when_available(self):
        agent = TechnicalAgent()

        def fake_ask(prompt, temperature=0.3):
            return """
            ```json
            {
              "signal": "bearish",
              "confidence": 0.91,
              "conclusion": "结构化结论：短线趋势转弱，先降级观察。",
              "key_points": ["JSON信号优先", "风险项明确"]
            }
            ```
            """

        agent._ask = fake_ask
        result = agent._analyze(
            "603501",
            {
                "name": "韦尔股份",
                "sector_name": "半导体",
                "indicators": {
                    "close": 100,
                    "ma5": 98,
                    "ma20": 105,
                    "rsi": 42,
                    "macd_bar": -0.12,
                    "vol_ratio": 0.8,
                },
            },
        )

        self.assertEqual(result.signal, "bearish")
        self.assertEqual(result.confidence, 0.91)
        self.assertEqual(result.conclusion, "结构化结论：短线趋势转弱，先降级观察。")
        self.assertIn("JSON信号优先", result.key_points)

    def test_legacy_signal_parser_is_shared_by_agent_base_class(self):
        agent = DummyAgent()

        signal, confidence = agent._parse_signal("【信号】neutral\n【置信度】1.25")
        self.assertEqual("neutral", signal)
        self.assertEqual(1.0, confidence)

        final_signal, final_confidence = agent._parse_signal("【最终信号】bearish\n【置信度】0.33", final=True)
        self.assertEqual("bearish", final_signal)
        self.assertEqual(0.33, final_confidence)

    def test_specialized_agents_do_not_duplicate_legacy_signal_parser(self):
        for agent_class in [
            TechnicalAgent,
            FundFlowAgent,
            RiskAgent,
            SectorAgent,
            ChokepointAgent,
            SynthesisAgent,
        ]:
            self.assertNotIn("_parse_signal", agent_class.__dict__, agent_class.__name__)


if __name__ == "__main__":
    unittest.main()
