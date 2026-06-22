import unittest

from nasdx.agents.technical import TechnicalAgent


class LLMStructuredContractsTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
