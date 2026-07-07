import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_security_checks.py"


def load_security_module():
    spec = importlib.util.spec_from_file_location("run_security_checks", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SecurityChecksContractsTest(unittest.TestCase):
    def test_security_script_is_lightweight_and_optional_by_default(self):
        self.assertTrue(SCRIPT.exists(), "run_security_checks.py is missing")
        text = SCRIPT.read_text(encoding="utf-8")

        for marker in [
            "secret_scan",
            "pip-audit",
            "bandit",
            "detect-secrets",
            "--run-optional",
            "--skip-optional",
            "git",
            "ls-files",
            "--exclude-standard",
        ]:
            self.assertIn(marker, text)

        self.assertNotIn("config.toml", text)
        self.assertNotIn("Start-Process", text)

    def test_secret_scan_ignores_generated_dirs_without_git(self):
        module = load_security_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exposed_key = "sk-" + "abcdefghijklmnopqrst"
            ignored_key = "sk-" + "zyxwvutsrqponmlkjihg"
            (root / "dist").mkdir()
            (root / "reports").mkdir()
            (root / "safe.py").write_text(f'KEY = "{exposed_key}"\n', encoding="utf-8")
            (root / "dist" / "ignored.py").write_text(f'KEY = "{ignored_key}"\n', encoding="utf-8")
            (root / "reports" / "ignored.md").write_text(f"{ignored_key}\n", encoding="utf-8")

            hits, scanned = module.scan_for_secrets(root)

        self.assertEqual(1, scanned)
        self.assertEqual(["safe.py:1:sk-abcde..."], hits)

    def test_cli_skip_optional_succeeds_without_external_security_tools(self):
        proc = subprocess.run(
            [sys.executable, "-B", "run_security_checks.py", "--skip-optional"],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=60,
        )

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("[PASS] secret_scan", proc.stdout)
        self.assertIn("[SKIP] pip-audit", proc.stdout)
        self.assertIn("[SKIP] bandit", proc.stdout)
        self.assertIn("[SKIP] detect-secrets", proc.stdout)


if __name__ == "__main__":
    unittest.main()
