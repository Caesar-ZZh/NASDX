import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_security_checks.py"

# Fake credential fragments, joined at runtime so no literal token is committed.
_FAKE_BODY = "Kq7fT2mZ9wB4nD8xR1vC5hJ3pL6yG0sA"
_FAKE_OPENAI_KEY = "sk-" + _FAKE_BODY
_FAKE_GITHUB_TOKEN = "ghp" + "_" + _FAKE_BODY + "2bQ7"


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
            "secret_history_scan",
            "pip-audit",
            "bandit",
            "detect-secrets",
            "--run-optional",
            "--skip-optional",
            "--history",
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
            (root / "dist").mkdir()
            (root / "reports").mkdir()
            (root / "safe.py").write_text(f'KEY = "{_FAKE_OPENAI_KEY}"\n', encoding="utf-8")
            (root / "dist" / "ignored.py").write_text(
                f'KEY = "{_FAKE_GITHUB_TOKEN}"\n', encoding="utf-8"
            )
            (root / "reports" / "ignored.md").write_text(
                f"{_FAKE_GITHUB_TOKEN}\n", encoding="utf-8"
            )

            hits, scanned = module.scan_for_secrets(root)

        self.assertEqual(1, scanned)
        self.assertEqual(1, len(hits))
        self.assertIn("rule=openai-style-api-key", hits[0])
        self.assertIn("path=safe.py", hits[0])
        self.assertIn("line=1", hits[0])
        self.assertIn("fingerprint=", hits[0])
        # Redacted output only: the credential must not leak into CI logs.
        self.assertNotIn(_FAKE_OPENAI_KEY, hits[0])
        self.assertNotIn(_FAKE_OPENAI_KEY[:8], hits[0])

    def test_secret_scan_detects_non_openai_providers(self):
        module = load_security_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ci.yml").write_text(f'token: "{_FAKE_GITHUB_TOKEN}"\n', encoding="utf-8")

            hits, scanned = module.scan_for_secrets(root)

        self.assertEqual(1, scanned)
        self.assertEqual(1, len(hits))
        self.assertIn("rule=github-token", hits[0])

    def test_cli_skip_optional_succeeds_without_external_security_tools(self):
        proc = subprocess.run(
            [sys.executable, "-B", "run_security_checks.py", "--skip-optional"],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=120,
        )

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("[PASS] secret_scan", proc.stdout)
        self.assertIn("[SKIP] pip-audit", proc.stdout)
        self.assertIn("[SKIP] bandit", proc.stdout)
        self.assertIn("[SKIP] detect-secrets", proc.stdout)

    def test_cli_history_mode_scans_reachable_blobs(self):
        proc = subprocess.run(
            [sys.executable, "-B", "run_security_checks.py", "--skip-optional", "--history"],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=300,
        )

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("[PASS] secret_scan", proc.stdout)
        self.assertIn("[PASS] secret_history_scan", proc.stdout)
        self.assertIn("reachable git blobs", proc.stdout)


if __name__ == "__main__":
    unittest.main()
