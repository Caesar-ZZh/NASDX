import time
import importlib
import os
import sys
import unittest
from pathlib import Path

import requests

from nasdx.environments.research import ResearchEnvironment
from nasdx.schema import AnalysisResult


ROOT = Path(__file__).resolve().parents[1]


class SleepingAgent:
    """Deterministic fake agent used to prove research concurrency."""

    def __init__(self, dimension: str, sleep_seconds: float = 0.2):
        self.dimension = dimension
        self.sleep_seconds = sleep_seconds

    def run(self, stock_code, stock_data):
        time.sleep(self.sleep_seconds)
        return AnalysisResult(
            agent_name=f"{self.dimension}_agent",
            dimension=self.dimension,
            conclusion=f"{stock_code} {self.dimension}",
            signal="neutral",
            confidence=0.5,
        )


class ArchitectureContractTests(unittest.TestCase):
    def test_research_environment_runs_phase_one_agents_concurrently(self):
        env = ResearchEnvironment(max_steps=1, delay=0, max_workers=5)
        env.agents = {
            dim: SleepingAgent(dim)
            for dim, _ in env.AGENT_ORDER
        }

        started = time.perf_counter()
        results = env.run("000001", {"name": "平安银行"}, verbose=False)
        elapsed = time.perf_counter() - started

        self.assertEqual(list(results), [dim for dim, _ in env.AGENT_ORDER])
        self.assertLess(
            elapsed,
            0.55,
            f"5 fake agents slept 0.2s each but run took {elapsed:.3f}s; phase one is not concurrent",
        )

    def test_streamlit_app_does_not_monkey_patch_requests_get(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")

        self.assertNotIn("_req.get = _patched_get", app_source)
        self.assertNotIn("_real_get = _req.get", app_source)
        self.assertNotIn("import requests as _req", app_source)

    def test_data_modules_do_not_patch_requests_or_proxy_env_on_import(self):
        proxy_keys = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]
        original_get = requests.get
        original_session_get = requests.Session.get
        original_env = {key: os.environ.get(key) for key in proxy_keys}
        sentinel_env = {key: "http://127.0.0.1:9000" for key in proxy_keys}

        try:
            os.environ.update(sentinel_env)
            for module_name in ("scripts.fetch_stock_data", "quant.data", "quant.patch_requests"):
                sys.modules.pop(module_name, None)
                importlib.import_module(module_name)

            self.assertIs(requests.get, original_get)
            self.assertIs(requests.Session.get, original_session_get)
            for key, expected in sentinel_env.items():
                self.assertEqual(os.environ.get(key), expected)
        finally:
            requests.get = original_get
            requests.Session.get = original_session_get
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
