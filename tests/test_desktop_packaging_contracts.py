import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROOT_DESKTOP_BAT = ROOT / "启动NASDX桌面.bat"
BUILD_SCRIPT = ROOT / "packaging" / "windows" / "build_portable.ps1"
BUILD_LAUNCHER_EXE_SCRIPT = ROOT / "packaging" / "windows" / "build_launcher_exe.ps1"
BUILD_INSTALLER_SCRIPT = ROOT / "packaging" / "windows" / "build_installer.ps1"
BUILD_ZIP_SCRIPT = ROOT / "packaging" / "windows" / "build_portable_zip.ps1"
WHEELHOUSE_SCRIPT = ROOT / "packaging" / "windows" / "build_wheelhouse.ps1"
SMOKE_SCRIPT = ROOT / "packaging" / "windows" / "smoke_portable.ps1"
INSTALLED_SMOKE_SCRIPT = ROOT / "packaging" / "windows" / "smoke_installed.ps1"
ZIP_SMOKE_SCRIPT = ROOT / "packaging" / "windows" / "smoke_portable_zip.ps1"
CREATE_SHORTCUTS_SCRIPT = ROOT / "packaging" / "windows" / "create_shortcuts.ps1"
ROUNDTRIP_SMOKE_SCRIPT = ROOT / "packaging" / "windows" / "smoke_installer_roundtrip.ps1"
INSTALL_INNO_SCRIPT = ROOT / "packaging" / "windows" / "install_inno_setup.ps1"
INSTALLER_PREFLIGHT_SCRIPT = ROOT / "packaging" / "windows" / "preflight_installer_release.ps1"
CONSTRAINTS_FILE = ROOT / "packaging" / "windows" / "constraints-win.txt"
INSTALLER_SCRIPT = ROOT / "packaging" / "windows" / "NASDX-Desktop.iss"


class DesktopPackagingContractsTest(unittest.TestCase):
    def test_root_desktop_batch_opens_control_panel_with_launcher_fallback(self):
        self.assertTrue(ROOT_DESKTOP_BAT.exists(), "启动NASDX桌面.bat is missing")
        text = ROOT_DESKTOP_BAT.read_text(encoding="utf-8")

        for marker in [
            "desktop\\control_panel.py",
            "desktop\\launcher.py --webview --page plan",
            ".venv\\Scripts\\python.exe",
            "python -B",
            "%*",
        ]:
            self.assertIn(marker, text)

        self.assertNotIn("streamlit run app.py", text)
        self.assertNotIn("sk-", text)

    def test_root_desktop_batch_supports_control_panel_dry_run(self):
        proc = subprocess.run(
            ["cmd", "/c", str(ROOT_DESKTOP_BAT), "--dry-run", "--page", "plan"],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        )
        payload = json.loads(proc.stdout)

        self.assertEqual(str(ROOT), payload["root"])
        self.assertEqual("plan", payload["page"])
        self.assertIn("Start", payload["actions"])
        self.assertIn("Data Refresh", payload["actions"])

    def test_packaging_script_exists_and_documents_exclusions(self):
        self.assertTrue(BUILD_SCRIPT.exists(), "build_portable.ps1 is missing")
        text = BUILD_SCRIPT.read_text(encoding="utf-8")

        for marker in [
            "reports/",
            "stock_data_*.json",
            "nasdx_history.db",
            "config.toml",
            ".env",
            "models/signal_confidence.json",
            "path_policy",
            "relative-or-redacted",
            "<source-checkout>",
            "package_root",
            "scrubbed_patterns",
            "Remove-PackageExcludedArtifacts",
            "KeepVenv",
            "desktop_logs/",
            "wheelhouse/",
            "fetch_log.txt",
            "*.pyc",
            "*.pyo",
            "Invoke-Checked",
            "--without-pip",
            "ensurepip",
            "nasdx-build-venv-",
            "IncludeWebView",
            "PipTimeout",
            "PipRetries",
            "--disable-pip-version-check",
            "--no-user",
            "--prefer-binary",
            "ConstraintsFile",
            "WheelhouseDir",
            "--no-index",
            "--find-links",
            "OnlyBinary",
            "--only-binary",
            "packaging/windows/constraints-win.txt",
            "packaging/windows/build_launcher_exe.ps1",
            "packaging/windows/create_shortcuts.ps1",
            "packaging/windows/smoke_installer_roundtrip.ps1",
            "%*",
        ]:
            self.assertIn(marker, text)

    def test_launcher_exe_build_script_is_optional_and_launcher_only(self):
        self.assertTrue(BUILD_LAUNCHER_EXE_SCRIPT.exists(), "build_launcher_exe.ps1 is missing")
        text = BUILD_LAUNCHER_EXE_SCRIPT.read_text(encoding="utf-8")

        for marker in [
            "PyInstaller",
            "SkipBuild",
            "plan-only mode",
            "desktop\\exe_launcher.py",
            "NASDX-Desktop-Launcher",
            "--onefile",
            "--distpath",
            "--workpath",
            "--specpath",
            "does not bundle app.py",
            ".venv\\Scripts\\python.exe",
        ]:
            self.assertIn(marker, text)

        self.assertNotIn("pip install", text)
        self.assertNotIn("Start-Process", text)
        self.assertNotIn("streamlit run app.py", text)
        self.assertNotIn("sk-", text)

    def test_launcher_exe_build_script_plan_only_does_not_require_pyinstaller(self):
        proc = subprocess.run(
            [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(BUILD_LAUNCHER_EXE_SCRIPT),
                "-SkipBuild",
            ],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertIn("NASDX launcher-only executable build", proc.stdout)
        self.assertIn("plan-only mode", proc.stdout)
        self.assertIn("python -m PyInstaller", proc.stdout)

    def test_legacy_windows_constraints_remain_available_for_compatibility(self):
        self.assertTrue(CONSTRAINTS_FILE.exists(), "constraints-win.txt is missing")
        text = CONSTRAINTS_FILE.read_text(encoding="utf-8")

        for marker in [
            "akshare==",
            "mootdx==",
            "pandas==",
            "numpy==",
            "openai==",
            "streamlit==",
            "tenacity==8.",
        ]:
            self.assertIn(marker, text)

        self.assertNotIn("sk-", text)

    def test_wheelhouse_script_documents_offline_dependency_path(self):
        self.assertTrue(WHEELHOUSE_SCRIPT.exists(), "build_wheelhouse.ps1 is missing")
        text = WHEELHOUSE_SCRIPT.read_text(encoding="utf-8")

        for marker in [
            "pip",
            "wheel",
            "wheelhouse\\nasdx-win-py311",
            "--wheel-dir",
            "requirements-win-core.lock",
            "requirements-win-webview.lock",
            "--require-hashes",
            "IncludeWebView",
            "constraints-win.txt",
        ]:
            self.assertIn(marker, text)

    def test_portable_zip_scripts_build_and_smoke_extracted_package(self):
        self.assertTrue(BUILD_ZIP_SCRIPT.exists(), "build_portable_zip.ps1 is missing")
        self.assertTrue(ZIP_SMOKE_SCRIPT.exists(), "smoke_portable_zip.ps1 is missing")
        build_text = BUILD_ZIP_SCRIPT.read_text(encoding="utf-8")
        smoke_text = ZIP_SMOKE_SCRIPT.read_text(encoding="utf-8")

        for marker in [
            "NASDX-Desktop-portable.zip",
            ".sha256",
            "nasdx_portable_release.v1",
            "Compress-Archive",
            "tar.exe",
            "Get-FileHash",
            "SHA256",
            "zip_sha256",
            "zip_size_bytes",
            "source_packaging_manifest",
            "scrubbed_patterns",
            "path_policy",
            "source_root",
            "package_root",
            "Get-ForbiddenPackageArtifacts",
            "forbidden artifact before zip",
            "RequireVenv",
            "PACKAGING_MANIFEST.json",
            "__pycache__",
            "*.pyc",
            "config.toml",
            ".env",
            "nasdx_history.db",
            "Refusing to write zip inside package directory",
        ]:
            self.assertIn(marker, build_text)

        for marker in [
            "NASDX-Desktop-portable.zip",
            "Expand-Archive",
            "tar.exe",
            "Get-FileHash",
            "nasdx_portable_release.v1",
            "zip_sha256",
            "zip_size_bytes",
            "Portable zip checksum verified",
            "Portable zip manifest verified",
            "smoke_installed.ps1",
            "RequireVenv",
            "Get-ForbiddenPackageArtifacts",
            "forbidden runtime/cache/build artifact",
            "__pycache__",
            "*.pyc",
            "config.toml",
            ".env",
            "nasdx_history.db",
            "reports",
            "NASDX portable zip smoke passed",
        ]:
            self.assertIn(marker, smoke_text)

        self.assertNotIn("Start-Process", build_text + smoke_text)
        self.assertNotIn("sk-", build_text + smoke_text)

    def test_portable_smoke_script_checks_package_startup_contract(self):
        self.assertTrue(SMOKE_SCRIPT.exists(), "smoke_portable.ps1 is missing")
        text = SMOKE_SCRIPT.read_text(encoding="utf-8")

        for marker in [
            "static\\style.css",
            "NASDX_RUNTIME_DIR",
            "desktop\\control_panel.py",
            "desktop\\doctor.py",
            "run_desktop_doctor.py",
            "run_desktop_completion_audit.py",
            "create_shortcuts.ps1",
            "启动NASDX桌面.bat",
            "RequireVenv",
            "Smoke python",
            "BatchDryRun",
            "--dry-run",
            "Data Refresh",
            "ConvertFrom-Json",
            "required_files",
            "preserved_entrypoints",
            "installer_roundtrip",
            "launch_plan",
            "plan-only mode",
            "--headless-smoke",
            "--page plan",
            "Get-CimInstance Win32_Process",
            "_smoke_runtime",
            "Remove-PythonCacheArtifacts",
            "__pycache__",
            "*.pyc",
            "*.pyo",
        ]:
            self.assertIn(marker, text)

    def test_installed_smoke_script_checks_installed_layout_without_running_installer(self):
        self.assertTrue(INSTALLED_SMOKE_SCRIPT.exists(), "smoke_installed.ps1 is missing")
        text = INSTALLED_SMOKE_SCRIPT.read_text(encoding="utf-8")

        for marker in [
            "Programs\\NASDX Desktop",
            "desktop\\control_panel.py",
            "desktop\\doctor.py",
            "run_desktop_doctor.py",
            "run_desktop_completion_audit.py",
            "create_shortcuts.ps1",
            "启动NASDX桌面.bat",
            "RequireVenv",
            "Smoke python",
            "BatchDryRun",
            "NASDX_RUNTIME_DIR",
            "--dry-run",
            "--headless-smoke",
            "Data Refresh",
            "ConvertFrom-Json",
            "required_files",
            "preserved_entrypoints",
            "installer_roundtrip",
            "launch_plan",
            "plan-only mode",
            "Get-CimInstance Win32_Process",
            "config.toml",
            ".env",
            "nasdx_history.db",
            "reports",
            "CheckShortcuts",
            "NASDX installed smoke passed",
            "Remove-PythonCacheArtifacts",
            "__pycache__",
            "*.pyc",
            "*.pyo",
        ]:
            self.assertIn(marker, text)

        self.assertNotIn("Start-Process", text)
        self.assertNotIn("NASDX-Desktop-Setup.exe", text)
        self.assertNotIn("sk-", text)

    def test_shortcut_script_is_plan_only_until_apply(self):
        self.assertTrue(CREATE_SHORTCUTS_SCRIPT.exists(), "create_shortcuts.ps1 is missing")
        text = CREATE_SHORTCUTS_SCRIPT.read_text(encoding="utf-8")

        for marker in [
            "plan-only mode",
            "Pass -Apply",
            "WScript.Shell",
            "CreateShortcut",
            "启动NASDX桌面.bat",
            "desktop\\control_panel.py",
            "Start Menu\\Programs\\NASDX Desktop",
            "[Environment]::GetFolderPath(\"Desktop\")",
            "Remove",
            "Launch NASDX Desktop",
        ]:
            self.assertIn(marker, text)

        self.assertNotIn("Start-Process", text)
        self.assertNotIn("streamlit run app.py", text)
        self.assertNotIn("sk-", text)

    def test_shortcut_script_plan_only_does_not_write_user_profile(self):
        proc = subprocess.run(
            [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(CREATE_SHORTCUTS_SCRIPT),
                "-Desktop",
            ],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertIn("plan-only mode", proc.stdout)
        self.assertIn("Pass -Apply", proc.stdout)
        self.assertIn("Would create shortcuts", proc.stdout)

    def test_windows_powershell_scripts_with_unicode_use_utf8_bom(self):
        scripts = [
            "build_installer.ps1",
            "build_portable.ps1",
            "build_portable_zip.ps1",
            "create_shortcuts.ps1",
            "preflight_installer_release.ps1",
            "smoke_installed.ps1",
            "smoke_portable.ps1",
            "smoke_portable_zip.ps1",
        ]
        for name in scripts:
            path = ROOT / "packaging" / "windows" / name
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"), name)

    def test_installer_roundtrip_script_requires_explicit_install_permission(self):
        self.assertTrue(ROUNDTRIP_SMOKE_SCRIPT.exists(), "smoke_installer_roundtrip.ps1 is missing")
        text = ROUNDTRIP_SMOKE_SCRIPT.read_text(encoding="utf-8")

        for marker in [
            "NASDX-Desktop-Setup.exe",
            "AllowInstall",
            "plan-only mode",
            "disposable Windows profile or VM",
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/DIR=",
            "smoke_installed.ps1",
            "CheckShortcuts",
            "RequireVenv",
            "ProofPath",
            "nasdx_installer_roundtrip_proof.v1",
            "installer_sha256",
            "Get-FileHash",
            "Roundtrip proof written",
            "proof was not written because -KeepInstalled skips uninstall",
            "KeepInstalled",
            "unins*.exe",
            "Refusing to install over the repository",
            "UsingDefaultTempInstallDir",
            "NASDX installer roundtrip passed",
        ]:
            self.assertIn(marker, text)

        self.assertIn("Start-Process", text)
        self.assertNotIn("sk-", text)

    def test_inno_setup_bootstrap_is_plan_only_until_explicit_install(self):
        self.assertTrue(INSTALL_INNO_SCRIPT.exists(), "install_inno_setup.ps1 is missing")
        text = INSTALL_INNO_SCRIPT.read_text(encoding="utf-8")

        for marker in [
            "JRSoftware.InnoSetup",
            "winget",
            "plan-only mode",
            "Install",
            "AcceptAgreements",
            "-Install -AcceptAgreements",
            "ISCC.exe",
        ]:
            self.assertIn(marker, text)

        self.assertNotIn("Start-Process", text)
        self.assertNotIn("sk-", text)

    def test_inno_setup_bootstrap_plan_only_does_not_install(self):
        proc = subprocess.run(
            [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(INSTALL_INNO_SCRIPT),
            ],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertTrue(
            "plan-only mode" in proc.stdout or "Inno Setup compiler found" in proc.stdout,
            proc.stdout,
        )
        self.assertNotIn("Installing", proc.stdout)

    def test_installer_release_preflight_is_read_only_and_documents_next_steps(self):
        self.assertTrue(INSTALLER_PREFLIGHT_SCRIPT.exists(), "preflight_installer_release.ps1 is missing")
        text = INSTALLER_PREFLIGHT_SCRIPT.read_text(encoding="utf-8")

        for marker in [
            "NASDX installer release preflight",
            "Strict",
            "RequireVenv",
            "Get-FileHash",
            "nasdx_portable_release.v1",
            "build_installer.ps1 -SkipPortableBuild",
            "smoke_installer_roundtrip.ps1",
            "NASDX-Desktop-roundtrip-proof.json",
            "AllowInstall",
            "CheckShortcuts",
            "ISCC.exe",
        ]:
            self.assertIn(marker, text)

        self.assertNotIn("Start-Process", text)
        self.assertNotIn("winget install", text)
        self.assertNotIn("sk-", text)

    def test_installer_release_preflight_default_is_non_mutating(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            proc = subprocess.run(
                [
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(INSTALLER_PREFLIGHT_SCRIPT),
                    "-PackageDir",
                    str(Path(temp_dir) / "not-built-yet"),
                    "-RequireVenv",
                ],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                check=True,
            )

        self.assertIn("NASDX installer release preflight", proc.stdout)
        self.assertIn("[INCOMPLETE] portable_package", proc.stdout)
        self.assertIn("Next compile command", proc.stdout)
        self.assertIn("Next roundtrip command", proc.stdout)
        self.assertIn("Expected roundtrip proof", proc.stdout)
        self.assertNotIn("Start-Process", proc.stdout)

    def test_installer_script_wraps_portable_package_without_user_artifacts(self):
        self.assertTrue(INSTALLER_SCRIPT.exists(), "NASDX-Desktop.iss is missing")
        text = INSTALLER_SCRIPT.read_text(encoding="utf-8")

        for marker in [
            "NASDX Desktop",
            "..\\..\\dist\\NASDX-Desktop",
            "..\\..\\dist\\installer",
            "启动NASDX桌面.bat",
            "build_portable.ps1",
            "iscc packaging\\windows\\NASDX-Desktop.iss",
            "PrivilegesRequired=lowest",
            "DefaultDirName={localappdata}\\Programs\\NASDX Desktop",
            "[Icons]",
            "{group}\\NASDX Desktop",
            "{autodesktop}\\NASDX Desktop",
            "Filename: \"{app}\\{#MyAppExeName}\"",
            "docs\\WINDOWS_DESKTOP.md",
            "WebView2",
            "reports\\*",
            "stock_data_*.json",
            "nasdx_history.db",
            "config.toml",
            ".env",
            "*.log",
            "wheelhouse\\*",
            "models\\signal_confidence.json",
            "%APPDATA%\\NASDX\\config.toml",
            "NASDX_CONFIG_FILE",
            "Do not delete local user runtime state on uninstall",
            "[UninstallDelete]",
            "Type: filesandordirs; Name: \"{app}\"",
            "#ifndef PortableDir",
            "#ifndef InstallerOutputDir",
        ]:
            self.assertIn(marker, text)

        self.assertNotIn("app.py\"; Description", text)
        self.assertNotIn("sk-", text)

    def test_installer_build_script_can_validate_without_compiling(self):
        self.assertTrue(BUILD_INSTALLER_SCRIPT.exists(), "build_installer.ps1 is missing")
        text = BUILD_INSTALLER_SCRIPT.read_text(encoding="utf-8")

        for marker in [
            "build_portable.ps1",
            "NASDX-Desktop.iss",
            "Get-IsccPath",
            "inno_paths.ps1",
            "SkipCompile",
            "SkipPortableBuild",
            "/DPortableDir",
            "/DInstallerOutputDir",
            "NASDX-Desktop-Setup.exe",
            "desktop\\control_panel.py",
            "Inno Setup 7/6",
        ]:
            self.assertIn(marker, text)

        self.assertNotIn("Start-Process", text)
        self.assertNotIn("sk-", text)

    def test_inno_path_helper_finds_path_and_registry_locations(self):
        helper = ROOT / "packaging" / "windows" / "inno_paths.ps1"
        self.assertTrue(helper.exists(), "inno_paths.ps1 is missing")
        text = helper.read_text(encoding="utf-8")

        for marker in [
            "Get-NasdxIsccPath",
            "Get-NasdxInnoSetupCandidates",
            "HKCU:",
            "WOW6432Node",
            "Inno Setup 7",
            "Inno Setup 6",
            "ISCC.exe",
        ]:
            self.assertIn(marker, text)

    def test_installer_build_skip_compile_checks_portable_package(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            package_dir = temp_path / "NASDX-Desktop"
            installer_dir = temp_path / "installer"
            subprocess.run(
                [
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(BUILD_SCRIPT),
                    "-OutputDir",
                    str(package_dir),
                    "-SkipDependencyInstall",
                ],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                check=True,
            )

            proc = subprocess.run(
                [
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(BUILD_INSTALLER_SCRIPT),
                    "-PackageDir",
                    str(package_dir),
                    "-InstallerOutputDir",
                    str(installer_dir),
                    "-SkipPortableBuild",
                    "-SkipCompile",
                ],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("NASDX installer validation passed.", proc.stdout)
            self.assertIn("Installer compile skipped.", proc.stdout)
            self.assertTrue(installer_dir.exists())
            self.assertFalse((installer_dir / "NASDX-Desktop-Setup.exe").exists())

    def test_portable_zip_build_rejects_cache_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            package_dir = temp_path / "NASDX-Desktop"
            subprocess.run(
                [
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(BUILD_SCRIPT),
                    "-OutputDir",
                    str(package_dir),
                    "-SkipDependencyInstall",
                ],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                check=True,
            )
            cache_dir = package_dir / "nasdx" / "__pycache__"
            cache_dir.mkdir(parents=True)
            (cache_dir / "leaked.cpython-311.pyc").write_bytes(b"redacted-package-secret")

            proc = subprocess.run(
                [
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(BUILD_ZIP_SCRIPT),
                    "-PackageDir",
                    str(package_dir),
                    "-OutputZip",
                    str(temp_path / "NASDX-Desktop-portable.zip"),
                ],
                cwd=str(ROOT),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(0, proc.returncode)
            self.assertIn("forbidden artifact before zip", proc.stderr + proc.stdout)
            self.assertIn("nasdx/__pycache__", (proc.stderr + proc.stdout).replace("\\", "/"))
            self.assertNotIn("redacted-package-secret", proc.stderr + proc.stdout)

    def test_installer_output_is_ignored(self):
        proc = subprocess.run(
            ["git", "check-ignore", "-q", "dist/installer/NASDX-Desktop-Setup.exe"],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertEqual(proc.returncode, 0, "installer output should be ignored under dist/")

    def test_final_audit_ignores_packaging_outputs(self):
        audit_source = (ROOT / "run_final_audit.py").read_text(encoding="utf-8")

        for marker in ['"dist"', '"build"', '"wheelhouse"', '".pytest_cache"', '".ruff_cache"']:
            self.assertIn(marker, audit_source)

    def test_package_output_patterns_are_ignored(self):
        ignored_paths = [
            "dist/NASDX-Desktop",
            "build/temp",
            "wheelhouse/example.whl",
            "desktop_logs/launcher.log",
            "models/signal_confidence.json",
        ]
        for path in ignored_paths:
            proc = subprocess.run(
                ["git", "check-ignore", "-q", path],
                cwd=str(ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.assertEqual(proc.returncode, 0, f"{path} should be ignored")

    def test_skip_dependency_build_copies_runtime_allowlist_and_excludes_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "NASDX-Desktop"
            subprocess.run(
                [
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(BUILD_SCRIPT),
                    "-OutputDir",
                    str(output_dir),
                    "-SkipDependencyInstall",
                ],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertTrue((output_dir / "app.py").exists())
            self.assertTrue((output_dir / "desktop" / "control.py").exists())
            self.assertTrue((output_dir / "desktop" / "control_panel.py").exists())
            self.assertTrue((output_dir / "desktop" / "exe_launcher.py").exists())
            self.assertTrue((output_dir / "desktop" / "launcher.py").exists())
            self.assertTrue((output_dir / "desktop" / "runtime.py").exists())
            self.assertTrue((output_dir / "static" / "style.css").exists())
            self.assertTrue((output_dir / "docs" / "WINDOWS_DESKTOP.md").exists())
            self.assertTrue((output_dir / "requirements_nasdx.txt").exists())
            self.assertTrue((output_dir / "requirements_desktop.txt").exists())
            self.assertTrue((output_dir / "run_desktop_completion_audit.py").exists())
            self.assertTrue((output_dir / "run_desktop_release_evidence.py").exists())
            self.assertTrue((output_dir / "启动NASDX桌面.bat").exists())
            self.assertTrue((output_dir / "packaging" / "windows" / "build_launcher_exe.ps1").exists())
            self.assertTrue((output_dir / "packaging" / "windows" / "constraints-win.txt").exists())
            self.assertTrue((output_dir / "packaging" / "windows" / "create_shortcuts.ps1").exists())
            self.assertTrue((output_dir / "packaging" / "windows" / "smoke_installed.ps1").exists())
            self.assertTrue((output_dir / "packaging" / "windows" / "smoke_installer_roundtrip.ps1").exists())

            for forbidden in [
                "reports",
                ".git",
                "nasdx_history.db",
                "config.toml",
                ".env",
                "stock_data_20260623.json",
                "models/signal_confidence.json",
            ]:
                self.assertFalse((output_dir / forbidden).exists(), f"{forbidden} should not be packaged")

            self.assertEqual([], list(output_dir.rglob("__pycache__")))
            self.assertEqual([], list(output_dir.rglob("*.pyc")))
            self.assertEqual([], list(output_dir.rglob("*.pyo")))

            manifest = json.loads((output_dir / "PACKAGING_MANIFEST.json").read_text(encoding="utf-8"))
            manifest_text = json.dumps(manifest, ensure_ascii=False)
            self.assertTrue(manifest["skip_dependency_install"])
            self.assertFalse(manifest["include_webview"])
            self.assertFalse(manifest["only_binary"])
            self.assertEqual("relative-or-redacted", manifest["path_policy"])
            self.assertEqual("<source-checkout>", manifest["source_root"])
            self.assertEqual(".", manifest["package_root"])
            self.assertNotIn("repo_root", manifest)
            self.assertNotIn("output_dir", manifest)
            self.assertNotIn(str(ROOT), manifest_text)
            self.assertNotIn(str(output_dir), manifest_text)
            self.assertNotIn(str(output_dir.parent), manifest_text)
            self.assertEqual("packaging/windows/constraints-win.txt", manifest["constraints_file"])
            self.assertIn("reports/", manifest["excluded_patterns"])
            self.assertIn("__pycache__/", manifest["scrubbed_patterns"])
            self.assertIn("*.pyc", manifest["scrubbed_patterns"])
            self.assertIn("desktop", manifest["included_directories"])
            self.assertIn("docs", manifest["included_directories"])
