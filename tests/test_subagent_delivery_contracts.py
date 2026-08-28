import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / ".claude" / "agents"

EXPECTED_AGENTS = {
    "upstream-skill-analyst.md": "上游方案拆解代理",
    "nasdx-feature-implementer.md": "单功能实现代理",
    "nasdx-contract-auditor.md": "契约审计代理",
    "nasdx-streamlit-verifier.md": "Streamlit 验证代理",
    "nasdx-delivery-closer.md": "交付收口代理",
}


class SubagentDeliveryContractsTest(unittest.TestCase):
    def test_five_subagent_templates_exist_with_safe_boundaries(self):
        for filename, label in EXPECTED_AGENTS.items():
            path = AGENT_DIR / filename
            self.assertTrue(path.exists(), f"missing subagent template: {filename}")
            text = path.read_text(encoding="utf-8")

            self.assertIn(label, text)
            self.assertIn("禁止", text)
            self.assertIn("验收", text)
            self.assertNotRegex(text, r"sk-[A-Za-z0-9_-]{20,}")

    def test_subagent_workflow_doc_links_agents_to_project_gates(self):
        path = ROOT / "docs" / "SUBAGENT_WORKFLOW.md"
        self.assertTrue(path.exists(), "missing subagent workflow doc")
        text = path.read_text(encoding="utf-8")

        for label in EXPECTED_AGENTS.values():
            self.assertIn(label, text)
        self.assertIn("run_final_audit.py", text)
        self.assertIn("run_product_readiness.py", text)
        self.assertIn("API Key", text)
        self.assertIn("不写入文件", text)

    def test_product_readiness_runner_is_importable_and_safe(self):
        path = ROOT / "scripts" / "run_product_readiness.py"
        self.assertTrue(path.exists(), "missing product readiness runner")
        text = path.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"sk-[A-Za-z0-9_-]{20,}")

        spec = importlib.util.spec_from_file_location("run_product_readiness", path)
        module = importlib.util.module_from_spec(spec)
        self.assertIsNotNone(spec.loader)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        commands = module.build_commands(include_llm_smoke=True)
        labels = [item.label for item in commands]
        self.assertIn("unit_tests", labels)
        self.assertIn("final_audit", labels)
        self.assertIn("llm_smoke", labels)


if __name__ == "__main__":
    unittest.main()
