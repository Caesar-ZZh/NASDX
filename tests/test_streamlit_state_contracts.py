import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StreamlitStateContractsTest(unittest.TestCase):
    def test_app_does_not_write_llm_config_to_process_environment(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")

        self.assertNotIn('os.environ["NASDX_API_KEY"]', source)
        self.assertNotIn('os.environ["NASDX_BASE_URL"]', source)
        self.assertNotIn('os.environ["NASDX_MODEL"]', source)
        self.assertNotIn("LLMClient._instance = None", source)

    def test_app_uses_public_llm_defaults_without_private_proxy_preset(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        llm_source = (ROOT / "nasdx" / "llm.py").read_text(encoding="utf-8")
        combined = app_source + "\n" + llm_source

        self.assertIn('"deepseek-chat"', combined)
        self.assertNotIn("deepseek-v4-pro", combined)
        self.assertNotIn("newapi.ecdigit.cn", app_source)
        self.assertNotIn("Claude 中转", app_source)

    def test_background_threads_are_not_stored_in_session_state(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")

        forbidden = [
            '"thread":None',
            "st.session_state.thread",
            '"etf50_scan_thread"',
            "st.session_state[\"etf50_scan_thread\"]",
        ]
        for marker in forbidden:
            self.assertNotIn(marker, source)

        self.assertIn("RUNNING_TASKS", source)
        self.assertIn("task_id", source)

    def test_analysis_logs_are_unique_per_task(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")

        self.assertNotIn('ROOT / f"nasdx_log_{code}.txt"', source)
        self.assertIn("nasdx_log_{code}_{task_id}.txt", source)


if __name__ == "__main__":
    unittest.main()
