import subprocess
import sys
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeliveryAssetsContractsTest(unittest.TestCase):
    def test_requirements_manifest_is_versionable(self):
        requirements_path = ROOT / "requirements_nasdx.txt"
        self.assertTrue(requirements_path.exists(), "requirements_nasdx.txt is missing")
        text = requirements_path.read_text(encoding="utf-8")
        self.assertIn("akshare", text)
        self.assertIn("mootdx", text)
        self.assertIn("tdxrs", text)
        self.assertIn("openai", text)
        self.assertIn("streamlit", text)

        proc = subprocess.run(
            ["git", "check-ignore", "-q", "requirements_nasdx.txt"],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertNotEqual(
            proc.returncode,
            0,
            "requirements_nasdx.txt is ignored by git even though README documents it as an install input",
        )

    def test_streamlit_version_avoids_sidebar_theme_console_regression(self):
        runtime_requirements = (ROOT / "requirements_nasdx.txt").read_text(encoding="utf-8")
        legacy_constraints = (ROOT / "packaging" / "windows" / "constraints-win.txt").read_text(encoding="utf-8")
        core_lock = (ROOT / "packaging" / "windows" / "requirements-win-core.lock").read_text(encoding="utf-8")
        webview_lock = (ROOT / "packaging" / "windows" / "requirements-win-webview.lock").read_text(encoding="utf-8")

        self.assertIn("streamlit>=1.59.2,<1.60.0", runtime_requirements)
        self.assertIn("streamlit==1.59.2", legacy_constraints)
        self.assertIn("streamlit==1.59.2", core_lock)
        self.assertIn("streamlit==1.59.2", webview_lock)

    def test_streamlit_theme_has_no_empty_color_values(self):
        config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))

        def assert_colors_are_set(section: dict, path: str) -> None:
            for key, value in section.items():
                item_path = f"{path}.{key}"
                if isinstance(value, dict):
                    assert_colors_are_set(value, item_path)
                elif "color" in key.lower():
                    self.assertIsInstance(value, str, f"{item_path} must be a color string")
                    self.assertTrue(value.strip(), f"{item_path} must not be empty")

        assert_colors_are_set(config.get("theme", {}), "theme")

    def test_development_tooling_manifest_is_versionable(self):
        requirements_path = ROOT / "requirements-dev.txt"
        self.assertTrue(requirements_path.exists(), "requirements-dev.txt is missing")
        text = requirements_path.read_text(encoding="utf-8")
        self.assertIn("-r requirements_nasdx.txt", text)
        self.assertIn("pytest", text)
        self.assertIn("ruff", text)
        self.assertIn("pre-commit", text)

        proc = subprocess.run(
            ["git", "check-ignore", "-q", "requirements-dev.txt"],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertNotEqual(
            proc.returncode,
            0,
            "requirements-dev.txt is ignored by git even though it is the Phase 1 dev install input",
        )

    def test_optional_desktop_manifest_is_versionable(self):
        requirements_path = ROOT / "requirements_desktop.txt"
        self.assertTrue(requirements_path.exists(), "requirements_desktop.txt is missing")
        text = requirements_path.read_text(encoding="utf-8")
        self.assertIn("-r requirements_nasdx.txt", text)
        self.assertIn("pywebview", text)

        proc = subprocess.run(
            ["git", "check-ignore", "-q", "requirements_desktop.txt"],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertNotEqual(
            proc.returncode,
            0,
            "requirements_desktop.txt is ignored by git even though it is the optional desktop install input",
        )

    def test_final_audit_checks_delivery_assets(self):
        audit_source = (ROOT / "run_final_audit.py").read_text(encoding="utf-8")
        self.assertIn("check_delivery_assets", audit_source)
        self.assertIn("check_desktop_delivery_assets", audit_source)
        self.assertIn("依赖清单", audit_source)
        self.assertIn("桌面交付资产", audit_source)
        self.assertIn("run_desktop_release_check.py", audit_source)
        self.assertIn("run_desktop_doctor.py", audit_source)
        self.assertIn("run_desktop_completion_audit.py", audit_source)
        self.assertIn("run_desktop_release_evidence.py", audit_source)
        self.assertIn("NASDX-desktop-release-evidence.json", audit_source)
        self.assertIn("--package-dir", audit_source)
        self.assertIn("--skip-zip", audit_source)
        self.assertIn("--write-evidence", audit_source)
        self.assertIn("--evidence-output", audit_source)
        self.assertIn("forbidden_present", audit_source)
        self.assertIn("package_forbidden_failures", audit_source)
        self.assertIn("zip_forbidden_failures", audit_source)
        self.assertIn("Remove-PackageExcludedArtifacts", audit_source)
        self.assertIn("scrubbed_patterns", audit_source)
        self.assertIn("path_policy", audit_source)
        self.assertIn("relative-or-redacted", audit_source)
        self.assertIn("__pycache__", audit_source)
        self.assertIn("*.pyc", audit_source)
        self.assertIn("run_security_checks.py", audit_source)
        self.assertIn("build_launcher_exe.ps1", audit_source)
        self.assertIn("desktop/exe_launcher.py", audit_source)
        self.assertIn("create_shortcuts.ps1", audit_source)
        self.assertIn("启动NASDX桌面.bat", audit_source)
        self.assertIn("smoke_installer_roundtrip.ps1", audit_source)
        self.assertIn("NASDX-Desktop-roundtrip-proof.json", audit_source)
        self.assertIn("nasdx_installer_roundtrip_proof.v1", audit_source)
        self.assertIn("install_inno_setup.ps1", audit_source)
        self.assertIn("preflight_installer_release.ps1", audit_source)
        self.assertIn(".github/workflows/windows-desktop.yml", audit_source)
        self.assertIn("run_desktop_release_check.py --skip-final-audit --fail-fast", audit_source)
        self.assertIn("build_installer.ps1 -SkipPortableBuild -SkipCompile", audit_source)
        self.assertIn("pip-audit", audit_source)

    def test_windows_desktop_guide_documents_safe_desktop_flow(self):
        guide_path = ROOT / "docs" / "WINDOWS_DESKTOP.md"
        self.assertTrue(guide_path.exists(), "docs/WINDOWS_DESKTOP.md is missing")
        text = guide_path.read_text(encoding="utf-8")

        for marker in [
            "desktop\\control_panel.py",
            "Start",
            "Stop",
            "Open App",
            "Settings",
            "Logs",
            "Data Refresh",
            "create_shortcuts.ps1",
            "run_desktop_doctor.py",
            "run_desktop_completion_audit.py",
            "run_desktop_release_evidence.py",
            "NASDX-desktop-release-evidence.json",
            "--package-dir",
            "--skip-zip",
            "--write-evidence",
            "--evidence-output",
            "forbidden_present",
            "package_forbidden_failures",
            "zip_forbidden_failures",
            "path_policy=relative-or-redacted",
            "__pycache__",
            "*.pyc",
            "--check-write",
            "启动NASDX桌面.bat",
            "desktop\\launcher.py --dry-run --page plan",
            "%APPDATA%\\NASDX\\config.toml",
            "NASDX_CONFIG_FILE",
            "NASDX_API_KEY",
            "build_portable.ps1 -SkipDependencyInstall",
            "build_launcher_exe.ps1",
            "desktop\\exe_launcher.py",
            "build_portable_zip.ps1",
            "NASDX-Desktop-portable.zip.sha256",
            "NASDX-Desktop-portable.manifest.json",
            "smoke_portable.ps1 -PackageDir dist\\NASDX-Desktop",
            "smoke_portable_zip.ps1",
            "smoke_installed.ps1 -InstallDir dist\\NASDX-Desktop",
            "smoke_installer_roundtrip.ps1",
            "NASDX-Desktop-roundtrip-proof.json",
            "nasdx_installer_roundtrip_proof.v1",
            "install_inno_setup.ps1",
            "preflight_installer_release.ps1",
            "-Install -AcceptAgreements",
            "-AllowInstall",
            "-RequireVenv",
            "$env:LOCALAPPDATA\\Programs\\NASDX Desktop",
            "build_wheelhouse.ps1",
            "build_installer.ps1 -SkipPortableBuild -SkipCompile",
            "build_installer.ps1 -SkipPortableBuild",
            "run_desktop_release_check.py",
            "run_security_checks.py",
            "--package-timeout",
            "--zip-timeout",
            "--pip-timeout",
            "--pip-retries",
            "--run-optional",
            "--full-package --compile-installer",
            ".github/workflows/windows-desktop.yml",
            "--skip-final-audit --fail-fast",
            "dist\\installer\\NASDX-Desktop-Setup.exe",
            "WebView2 Runtime",
            "run_final_audit.py",
            "Do not commit",
        ]:
            self.assertIn(marker, text)

        self.assertNotIn("sk-", text)
        self.assertIn("config.toml", (ROOT / ".gitignore").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
