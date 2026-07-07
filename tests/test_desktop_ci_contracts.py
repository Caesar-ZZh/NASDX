import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "windows-desktop.yml"


class DesktopCiContractsTest(unittest.TestCase):
    def test_windows_desktop_workflow_runs_safe_release_check(self):
        self.assertTrue(WORKFLOW.exists(), "windows-desktop.yml is missing")
        text = WORKFLOW.read_text(encoding="utf-8")

        for marker in [
            "windows-latest",
            "actions/checkout@v4",
            "actions/setup-python@v5",
            'python-version: "3.11"',
            "python -m pip install -r requirements-dev.txt",
            "python -B run_desktop_release_check.py --skip-final-audit --fail-fast",
            "tests/test_delivery_assets_contracts.py",
            "tests/test_desktop_release_check_contracts.py",
        ]:
            self.assertIn(marker, text)

        self.assertNotIn("--compile-installer", text)
        self.assertNotIn("NASDX-Desktop-Setup.exe", text)
        self.assertNotIn("Start-Process", text)
        self.assertNotIn("sk-", text)


if __name__ == "__main__":
    unittest.main()
