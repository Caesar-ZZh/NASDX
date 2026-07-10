import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_desktop_release_check.py"


def load_release_module():
    spec = importlib.util.spec_from_file_location("run_desktop_release_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DesktopReleaseCheckContractsTest(unittest.TestCase):
    def test_release_check_script_is_versionable_and_safe_by_default(self):
        self.assertTrue(SCRIPT.exists(), "run_desktop_release_check.py is missing")
        text = SCRIPT.read_text(encoding="utf-8")

        for marker in [
            "build_portable.ps1",
            "smoke_portable.ps1",
            "smoke_installed.ps1",
            "build_installer.ps1",
            "build_portable_zip.ps1",
            "smoke_portable_zip.ps1",
            "NASDX-Desktop-portable.zip.sha256",
            "NASDX-Desktop-portable.manifest.json",
            "run_security_checks.py",
            "run_desktop_doctor.py",
            "run_desktop_completion_audit.py",
            "run_desktop_release_evidence.py",
            "--package-dir",
            "--skip-zip",
            "--write-evidence",
            "--evidence-output",
            "dist\\\\release-evidence\\\\NASDX-desktop-release-evidence.json",
            "--json",
            "--skip-optional",
            "-SkipCompile",
            "run_final_audit.py",
            "tests/test_desktop_launcher_contracts.py",
            "tests/test_desktop_control_contracts.py",
            "tests/test_desktop_packaging_contracts.py",
            "tests/test_desktop_completion_audit_contracts.py",
            "tests/test_desktop_release_evidence_contracts.py",
            "--compile-installer",
            "--zip-package",
            "--zip-timeout",
            "--package-timeout",
            "--pip-timeout",
            "--pip-retries",
            "NASDX-Desktop-check",
            "TimeoutExpired",
            "never runs the installer",
        ]:
            self.assertIn(marker, text)

        self.assertNotIn("Start-Process", text)
        self.assertNotIn("NASDX-Desktop-Setup.exe", text)
        self.assertNotIn("sk-", text)

    def test_default_release_commands_validate_desktop_package_without_installer_compile(self):
        module = load_release_module()

        commands = module.build_commands()
        labels = [item.label for item in commands]
        joined = "\n".join(" ".join(item.argv) for item in commands)

        self.assertEqual(
            [
                "ruff",
                "desktop_contracts",
                "security_checks",
                "desktop_doctor",
                "desktop_completion_audit",
                "portable_package",
                "portable_smoke",
                "installed_layout_smoke",
                "installer_inputs",
                "release_evidence",
                "final_audit",
            ],
            labels,
        )
        self.assertIn("-SkipDependencyInstall", joined)
        self.assertIn("-SkipCompile", joined)
        self.assertIn("dist\\NASDX-Desktop-check", joined)
        self.assertNotIn("dist\\NASDX-Desktop -Timeout", joined)
        self.assertIn("smoke_installed.ps1", joined)
        self.assertIn("run_desktop_release_evidence.py --json --package-dir dist\\NASDX-Desktop-check", joined)
        self.assertIn("--skip-zip", joined)
        self.assertNotIn("--write", joined)
        self.assertNotIn("--output", joined)
        self.assertNotIn("-RequireVenv", joined)
        self.assertNotIn("build_portable_zip.ps1", joined)
        self.assertNotIn("--zip-path", joined)
        self.assertNotIn("--llm-smoke", joined)

    def test_full_compile_options_are_explicit(self):
        module = load_release_module()

        commands = module.build_commands(
            full_package=True,
            include_webview=True,
            compile_installer=True,
            pip_timeout=120,
            pip_retries=3,
        )
        joined = "\n".join(" ".join(item.argv) for item in commands)

        self.assertNotIn("-SkipDependencyInstall", joined)
        self.assertIn("-IncludeWebView", joined)
        self.assertIn("-PipTimeout 120", joined)
        self.assertIn("-PipRetries 3", joined)
        self.assertIn("dist\\NASDX-Desktop", joined)
        self.assertNotIn("dist\\NASDX-Desktop-check", joined)
        self.assertIn("-RequireVenv", joined)
        self.assertNotIn("-SkipCompile", joined)
        self.assertEqual(900, next(item.timeout for item in commands if item.label == "portable_package"))

    def test_release_evidence_write_is_explicit_and_targets_selected_package(self):
        module = load_release_module()

        commands = module.build_commands(
            write_evidence=True,
            evidence_output="dist\\release-evidence\\custom-evidence.json",
        )
        release_command = next(item for item in commands if item.label == "release_evidence")
        joined = " ".join(release_command.argv)

        self.assertIn("run_desktop_release_evidence.py", joined)
        self.assertIn("--write", release_command.argv)
        self.assertIn("--output", release_command.argv)
        self.assertIn("dist\\release-evidence\\custom-evidence.json", release_command.argv)
        self.assertIn("--package-dir", release_command.argv)
        self.assertIn("dist\\NASDX-Desktop-check", release_command.argv)
        self.assertNotIn("--json", release_command.argv)

    def test_zip_package_options_are_explicit_and_require_venv_for_full_package(self):
        module = load_release_module()

        commands = module.build_commands(full_package=True, zip_package=True, zip_timeout=777)
        labels = [item.label for item in commands]
        joined = "\n".join(" ".join(item.argv) for item in commands)

        self.assertIn("portable_zip", labels)
        self.assertIn("portable_zip_smoke", labels)
        self.assertLess(labels.index("installed_layout_smoke"), labels.index("portable_zip"))
        self.assertLess(labels.index("portable_zip_smoke"), labels.index("installer_inputs"))
        self.assertLess(labels.index("installer_inputs"), labels.index("release_evidence"))
        self.assertIn("build_portable_zip.ps1", joined)
        self.assertIn("smoke_portable_zip.ps1", joined)
        self.assertIn("dist\\NASDX-Desktop-portable.zip", joined)
        self.assertIn("dist\\NASDX-Desktop-portable.zip.sha256", joined)
        self.assertIn("dist\\NASDX-Desktop-portable.manifest.json", joined)
        self.assertIn("--zip-path", joined)
        self.assertIn("--zip-manifest", joined)
        self.assertNotIn("--skip-zip", joined)
        self.assertIn("-RequireVenv", joined)
        self.assertEqual(777, next(item.timeout for item in commands if item.label == "portable_zip"))
        self.assertEqual(777, next(item.timeout for item in commands if item.label == "portable_zip_smoke"))

    def test_timeout_is_reported_as_failed_result_instead_of_traceback(self):
        module = load_release_module()
        result = module.run_command(module.CommandSpec(label="timeout_probe", argv=[sys.executable, "-c", "import time; time.sleep(2)"], timeout=1))

        self.assertEqual("timeout_probe", result.label)
        self.assertEqual(124, result.returncode)
        self.assertIn("timed out after 1s", result.output_tail)


if __name__ == "__main__":
    unittest.main()
