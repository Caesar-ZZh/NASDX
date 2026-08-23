import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.run_investment_workflow import _select_top_selector_code, _step_command


ROOT = Path(__file__).resolve().parents[1]


class InvestmentWorkflowContractsTest(unittest.TestCase):
    def test_selector_workflow_selects_first_available_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "stock_selector_latest.json"
            report.write_text(
                json.dumps(
                    {
                        "candidates": {
                            "tier_a": [],
                            "tier_b": [{"code": "1", "name": "平安银行"}],
                            "pullback": [{"code": "600519", "name": "贵州茅台"}],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self.assertEqual("000001", _select_top_selector_code(report))

    def test_selector_workflow_returns_none_when_no_candidate_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "stock_selector_latest.json"
            report.write_text(json.dumps({"candidates": {"tier_a": []}}), encoding="utf-8")

            self.assertIsNone(_select_top_selector_code(report))

    def test_analysis_command_rejects_missing_stock_code(self):
        with self.assertRaises(ValueError):
            _step_command("analysis", None, rounds=1, risk_profile="balanced", analysis_mode="rules")

    def test_selector_dry_run_uses_candidate_placeholder_not_none(self):
        proc = subprocess.run(
            [
                sys.executable,
                "-B",
                "scripts/run_investment_workflow.py",
                "--workflow",
                "selector",
                "--dry-run",
            ],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=30,
        )

        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("<selector-top-candidate>", proc.stdout)
        self.assertNotIn("None", proc.stdout)


if __name__ == "__main__":
    unittest.main()
