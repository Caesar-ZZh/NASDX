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
            "PYTHONIOENCODING: utf-8",
            'PYTHONUTF8: "1"',
            # Action 固定到 commit SHA（#72），版本号只保留在尾注中。
            "actions/checkout@08c6903cd8c0fde910a37f88322edcfb5dd907a8 # v5.0.0",
            "actions/setup-python@e797f83bcb11b83ae66e0230d6156d7c80228e7c # v6.0.0",
            'python-version: "3.11.9"',
            'python -m pip install "pip==25.3" "uv==0.10.2"',
            "python -B run_dependency_lock_check.py --enforce-toolchain",
            "python -m pip install -r requirements-dev.txt",
            "python -B run_desktop_release_check.py --skip-final-audit --fail-fast",
            "tests/test_delivery_assets_contracts.py",
            "tests/test_desktop_release_check_contracts.py",
        ]:
            self.assertIn(marker, text)

        self.assertNotIn("--compile-installer", text)
        self.assertNotIn("actions/checkout@v", text)
        self.assertNotIn("actions/setup-python@v", text)
        self.assertNotIn("NASDX-Desktop-Setup.exe", text)
        self.assertNotIn("Start-Process", text)
        self.assertNotIn("sk-", text)

    def test_secret_scan_runs_before_dependency_install_and_release_checks(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        secret_scan = text.index("python -B run_security_checks.py --skip-optional")
        dependency_install = text.index("python -m pip install -r requirements-dev.txt")
        release_check = text.index("python -B run_desktop_release_check.py")
        self.assertLess(secret_scan, dependency_install)
        self.assertLess(secret_scan, release_check)


if __name__ == "__main__":
    unittest.main()
