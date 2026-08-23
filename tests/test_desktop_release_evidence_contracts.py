import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_desktop_release_evidence.py"


def create_clean_package(package_dir: Path, *, name: str = "NASDX-Desktop-check") -> None:
    (package_dir / "desktop").mkdir(parents=True)
    (package_dir / ".venv" / "Scripts").mkdir(parents=True)
    (package_dir / "PACKAGING_MANIFEST.json").write_text(
        json.dumps({"name": name, "skip_dependency_install": True}),
        encoding="utf-8",
    )
    (package_dir / "启动NASDX桌面.bat").write_text("@echo off\n", encoding="utf-8")
    (package_dir / "desktop" / "exe_launcher.py").write_text("print('shim')\n", encoding="utf-8")
    (package_dir / ".venv" / "Scripts" / "python.exe").write_bytes(b"")


class DesktopReleaseEvidenceContractsTest(unittest.TestCase):
    def test_release_evidence_script_is_read_only_and_secret_safe(self):
        self.assertTrue(SCRIPT.exists(), "run_desktop_release_evidence.py is missing")
        text = SCRIPT.read_text(encoding="utf-8")

        for marker in [
            "nasdx_desktop_release_evidence.v1",
            "run_completion_audit",
            "run_doctor",
            "dist/release-evidence/NASDX-desktop-release-evidence.json",
            "NASDX-Desktop-roundtrip-proof.json",
            "NASDX-Desktop-portable.manifest.json",
            "next_commands",
            "--package-dir",
            "--skip-zip",
            "--strict",
            "FORBIDDEN_PACKAGE_PATTERNS",
            "forbidden_present",
            "package_forbidden_failures",
            "zip_forbidden_failures",
            "_zip_forbidden_entries",
            "path_policy",
            "source_root",
            "package_root",
            "__pycache__",
            "*.pyc",
        ]:
            self.assertIn(marker, text)

        for forbidden in ["Start-Process", "streamlit run app.py", "pip install", "requests.", "sk-"]:
            self.assertNotIn(forbidden, text)

    def test_release_evidence_json_is_machine_readable_and_secret_safe(self):
        secret = "s" + "k-" + "releaseevidencesecretvalue"
        env = dict(os.environ)
        env["NASDX_API_KEY"] = secret

        with tempfile.TemporaryDirectory() as temp_dir:
            package_dir = Path(temp_dir) / "NASDX-Desktop-check"
            create_clean_package(package_dir)
            proc = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "scripts/run_desktop_release_evidence.py",
                    "--json",
                    "--package-dir",
                    str(package_dir),
                    "--zip-path",
                    str(Path(temp_dir) / "missing-portable.zip"),
                ],
                cwd=str(ROOT),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=True,
            )
            payload = json.loads(proc.stdout)

        self.assertEqual("nasdx_desktop_release_evidence.v1", payload["schema"])
        for key in [
            "generated_at",
            "root",
            "completion_audit",
            "desktop_doctor",
            "artifacts",
            "ignored_paths",
            "next_commands",
            "summary",
        ]:
            self.assertIn(key, payload)

        self.assertTrue(any(item["label"] == "generated_files_excluded" for item in payload["completion_audit"]))
        self.assertEqual(str(package_dir), payload["artifacts"]["portable_package"]["path"])
        self.assertIn("forbidden_present", payload["artifacts"]["portable_package"])
        self.assertIn("package_forbidden_failures", payload["summary"])
        self.assertIn("forbidden_present", payload["artifacts"]["portable_zip"])
        self.assertIn("zip_forbidden_failures", payload["summary"])
        self.assertTrue(any(item["path"] == "dist/release-evidence/NASDX-desktop-release-evidence.json" for item in payload["ignored_paths"]))
        self.assertIn("NASDX-Desktop-roundtrip-proof.json", json.dumps(payload["artifacts"], ensure_ascii=False))
        self.assertNotIn(secret, proc.stdout)
        self.assertNotIn("releaseevidencesecretvalue", proc.stdout)

    def test_release_evidence_can_target_the_package_under_test(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            package_dir = Path(temp_dir) / "NASDX-Desktop-check"
            create_clean_package(package_dir)

            proc = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "scripts/run_desktop_release_evidence.py",
                    "--json",
                    "--package-dir",
                    str(package_dir),
                    "--zip-path",
                    str(package_dir.parent / "missing-portable.zip"),
                ],
                cwd=str(ROOT),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=True,
            )
            payload = json.loads(proc.stdout)
            portable = payload["artifacts"]["portable_package"]

        self.assertEqual(str(package_dir), portable["path"])
        self.assertTrue(portable["exists"])
        self.assertTrue(portable["bundled_python"])
        self.assertTrue(portable["desktop_entry"])
        self.assertTrue(portable["launcher_exe_entry"])
        self.assertEqual([], portable["forbidden_present"])
        self.assertEqual("NASDX-Desktop-check", portable["manifest"]["name"])

    def test_release_evidence_fails_when_package_contains_forbidden_runtime_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            package_dir = Path(temp_dir) / "NASDX-Desktop-check"
            package_dir.mkdir()
            (package_dir / "PACKAGING_MANIFEST.json").write_text(
                json.dumps({"name": "NASDX-Desktop-check", "skip_dependency_install": True}),
                encoding="utf-8",
            )
            (package_dir / ".env").write_text("NASDX_API_KEY=redacted-package-secret\n", encoding="utf-8")
            (package_dir / "nasdx" / "__pycache__").mkdir(parents=True)
            (package_dir / "nasdx" / "__pycache__" / "leaked.cpython-311.pyc").write_bytes(
                b"redacted-package-secret"
            )

            proc = subprocess.run(
                [sys.executable, "-B", "scripts/run_desktop_release_evidence.py", "--json", "--package-dir", str(package_dir)],
                cwd=str(ROOT),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
            payload = json.loads(proc.stdout)
            portable = payload["artifacts"]["portable_package"]

        self.assertNotEqual(0, proc.returncode)
        self.assertIn(".env", portable["forbidden_present"])
        self.assertIn("nasdx/__pycache__", portable["forbidden_present"])
        self.assertIn("nasdx/__pycache__/leaked.cpython-311.pyc", portable["forbidden_present"])
        self.assertGreater(payload["summary"]["package_forbidden_failures"], 0)
        self.assertNotIn("redacted-package-secret", proc.stdout)

    def test_release_evidence_fails_when_portable_zip_contains_forbidden_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            package_dir = temp_path / "NASDX-Desktop-check"
            create_clean_package(package_dir)
            zip_path = temp_path / "NASDX-Desktop-portable.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("NASDX-Desktop/app.py", "print('ok')\n")
                archive.writestr("NASDX-Desktop/nasdx/__pycache__/", b"")
                archive.writestr("NASDX-Desktop/nasdx/__pycache__/leaked.cpython-311.pyc", b"redacted-zip-secret")
                archive.writestr("NASDX-Desktop/.env", "NASDX_API_KEY=redacted-zip-secret\n")

            proc = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "scripts/run_desktop_release_evidence.py",
                    "--json",
                    "--package-dir",
                    str(package_dir),
                    "--zip-path",
                    str(zip_path),
                ],
                cwd=str(ROOT),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
            payload = json.loads(proc.stdout)
            portable_zip = payload["artifacts"]["portable_zip"]

        self.assertNotEqual(0, proc.returncode)
        self.assertIn("NASDX-Desktop/.env", portable_zip["forbidden_present"])
        self.assertIn("NASDX-Desktop/nasdx/__pycache__", portable_zip["forbidden_present"])
        self.assertIn("NASDX-Desktop/nasdx/__pycache__/leaked.cpython-311.pyc", portable_zip["forbidden_present"])
        self.assertGreater(payload["summary"]["zip_forbidden_failures"], 0)
        self.assertNotIn("redacted-zip-secret", proc.stdout)

    def test_release_evidence_write_mode_uses_explicit_output_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "evidence.json"
            package_dir = Path(temp_dir) / "NASDX-Desktop-check"
            create_clean_package(package_dir)
            proc = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "scripts/run_desktop_release_evidence.py",
                    "--write",
                    "--output",
                    str(output_path),
                    "--package-dir",
                    str(package_dir),
                    "--zip-path",
                    str(Path(temp_dir) / "missing-portable.zip"),
                ],
                cwd=str(ROOT),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=True,
            )

            self.assertTrue(output_path.exists())
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual("nasdx_desktop_release_evidence.v1", payload["schema"])
            self.assertIn(str(output_path), proc.stdout)
            self.assertNotIn("sk-", output_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
