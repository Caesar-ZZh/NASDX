import time
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

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
    def test_cosmos_release_version_is_synchronized(self):
        expected = "0.3.1"
        package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
        package_lock = json.loads((ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
        layout_source = (ROOT / "frontend" / "src" / "components" / "layout" / "Layout.tsx").read_text(
            encoding="utf-8"
        )
        api_source = (ROOT / "server" / "stock" / "base_app.py").read_text(encoding="utf-8")

        layout_match = re.search(r'APP_VERSION = "v([^"]+)"', layout_source)
        fastapi_match = re.search(r'FastAPI\(title="Cosmos API", version="([^"]+)"\)', api_source)
        health_match = re.search(r'"service": "cosmos-api", "version": "([^"]+)"', api_source)

        self.assertIsNotNone(layout_match)
        self.assertIsNotNone(fastapi_match)
        self.assertIsNotNone(health_match)
        self.assertEqual(
            {
                package["version"],
                package_lock["version"],
                package_lock["packages"][""]["version"],
                layout_match.group(1),
                fastapi_match.group(1),
                health_match.group(1),
            },
            {expected},
        )

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
        sentinel = "http://127.0.0.1:9000"
        env = os.environ.copy()
        env.update({key: sentinel for key in proxy_keys})
        probe = (
            "import importlib, os, requests; "
            "before=(requests.get, requests.Session.get); "
            "[importlib.import_module(name) for name in "
            "('scripts.fetch_stock_data','quant.data','quant.patch_requests')]; "
            "assert before == (requests.get, requests.Session.get); "
            f"assert all(os.environ.get(key) == {sentinel!r} for key in {proxy_keys!r})"
        )
        proc = subprocess.run(
            [sys.executable, "-B", "-c", probe],
            cwd=str(ROOT),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
