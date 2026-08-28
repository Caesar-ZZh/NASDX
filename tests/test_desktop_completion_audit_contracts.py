import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_desktop_completion_audit.py"


class DesktopCompletionAuditContractsTest(unittest.TestCase):
    def test_completion_audit_script_is_read_only_and_documents_known_gaps(self):
        self.assertTrue(SCRIPT.exists(), "run_desktop_completion_audit.py is missing")
        text = SCRIPT.read_text(encoding="utf-8")

        for marker in [
            "hashlib",
            "run_completion_audit",
            "preserved_entrypoints",
            "desktop_launcher_mvp",
            "safe_local_config",
            "build_launcher_exe.ps1",
            "exe_launcher.py",
            "generated_files_excluded",
            "NASDX-Desktop-portable.zip.sha256",
            "NASDX-Desktop-portable.manifest.json",
            "portable_runtime_bundle",
            "installer_compile_tool",
            "installer_roundtrip",
            "NASDX-Desktop-roundtrip-proof.json",
            "nasdx_installer_roundtrip_proof.v1",
            "installer_sha256",
            "INCOMPLETE",
            "ISCC.exe",
            "install_inno_setup.ps1",
            "preflight_installer_release.ps1",
            "smoke_installer_roundtrip.ps1 -AllowInstall -CheckShortcuts -RequireVenv",
            "run_desktop_release_evidence.py",
            "tests/test_desktop_release_evidence_contracts.py",
            "dist/release-evidence/NASDX-desktop-release-evidence.json",
            "--package-dir",
            "--write-evidence",
            "--evidence-output",
            "_package_cache_artifacts",
            "__pycache__",
            "*.pyc",
        ]:
            self.assertIn(marker, text)

        for forbidden in ["Start-Process", "streamlit run app.py", "pip install"]:
            self.assertNotIn(forbidden, text)
        self.assertNotIn("sk-", text)

    def test_completion_audit_json_is_machine_readable_and_secret_safe(self):
        secret = "s" + "k-" + "completionauditsecretvalue"
        env = dict(os.environ)
        env["NASDX_API_KEY"] = secret

        proc = subprocess.run(
            [sys.executable, "-B", "run_desktop_completion_audit.py", "--json"],
            cwd=str(ROOT),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        )
        payload = json.loads(proc.stdout)
        by_label = {item["label"]: item for item in payload}

        expected_labels = {
            "preserved_entrypoints",
            "desktop_launcher_mvp",
            "safe_local_config",
            "packaging_chain",
            "portable_runtime_bundle",
            "test_release_gates",
            "generated_files_excluded",
            "optional_webview",
            "installer_compile_tool",
            "installer_roundtrip",
        }
        self.assertEqual(expected_labels, set(by_label))
        self.assertEqual("PASS", by_label["preserved_entrypoints"]["status"])
        self.assertEqual("PASS", by_label["desktop_launcher_mvp"]["status"])
        self.assertEqual("PASS", by_label["safe_local_config"]["status"])
        self.assertEqual("PASS", by_label["generated_files_excluded"]["status"])
        self.assertIn(by_label["portable_runtime_bundle"]["status"], {"PASS", "INCOMPLETE"})
        self.assertIn(by_label["optional_webview"]["status"], {"PASS", "WARN"})
        self.assertIn(by_label["installer_compile_tool"]["status"], {"PASS", "INCOMPLETE"})
        self.assertIn(by_label["installer_roundtrip"]["status"], {"PASS", "WARN", "INCOMPLETE"})
        self.assertNotIn(secret, proc.stdout)
        self.assertNotIn("completionauditsecretvalue", proc.stdout)

    def test_installer_roundtrip_proof_requires_current_installer_hash(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("run_desktop_completion_audit", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            installer_dir = root / "dist" / "installer"
            installer_dir.mkdir(parents=True)
            installer = installer_dir / "NASDX-Desktop-Setup.exe"
            installer.write_bytes(b"nasdx setup bytes")
            proof = installer_dir / "NASDX-Desktop-roundtrip-proof.json"
            proof.write_text(
                json.dumps(
                    {
                        "schema": "nasdx_installer_roundtrip_proof.v1",
                        "installer_sha256": "not-the-current-hash",
                        "require_venv": True,
                        "check_shortcuts": True,
                        "installed_smoke": "passed",
                        "uninstall": "passed",
                        "kept_installed": False,
                    }
                ),
                encoding="utf-8",
            )

            item = module._check_installer_roundtrip_proof(installer, proof)

        self.assertEqual("FAIL", item.status)
        self.assertIn("hash", item.evidence)


if __name__ == "__main__":
    unittest.main()
