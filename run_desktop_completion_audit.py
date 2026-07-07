"""Summarize NASDX Windows desktop completion evidence without mutating state."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from desktop.inno import find_iscc

ROOT = Path(__file__).parent

PASS = "PASS"
WARN = "WARN"
INCOMPLETE = "INCOMPLETE"
FAIL = "FAIL"


@dataclass(frozen=True)
class CompletionItem:
    label: str
    status: str
    evidence: str
    next_step: str


def run_completion_audit(root: Path = ROOT) -> list[CompletionItem]:
    return [
        _check_preserved_entrypoints(root),
        _check_desktop_launcher_mvp(root),
        _check_safe_local_config(root),
        _check_packaging_chain(root),
        _check_portable_runtime_bundle(root),
        _check_test_release_gates(root),
        _check_generated_files_excluded(root),
        _check_optional_webview(),
        _check_installer_compile_tool(),
        _check_installer_roundtrip(root),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Report NASDX Windows desktop completion evidence.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--strict", action="store_true", help="Treat INCOMPLETE items as a non-zero result.")
    args = parser.parse_args()

    items = run_completion_audit(ROOT)
    if args.json:
        print(json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2))
    else:
        print("NASDX desktop completion audit")
        print("=" * 72)
        for item in items:
            print(f"[{item.status}] {item.label}: {item.evidence}")
            if item.status != PASS:
                print(f"  next: {item.next_step}")
        print("=" * 72)
        counts = {status: sum(1 for item in items if item.status == status) for status in _statuses()}
        print(
            "summary: "
            f"passed={counts[PASS]} warned={counts[WARN]} "
            f"incomplete={counts[INCOMPLETE]} failed={counts[FAIL]}"
        )

    if any(item.status == FAIL for item in items):
        return 1
    if args.strict and any(item.status == INCOMPLETE for item in items):
        return 1
    return 0


def _check_preserved_entrypoints(root: Path) -> CompletionItem:
    required = [
        "app.py",
        "启动网页.bat",
        "fetch_stock_data.py",
        "scan_etf50.py",
        "scan_stocks_full.py",
        "run_analysis.py",
        "run_investment_workflow.py",
        "run_portfolio_plan.py",
        "run_final_audit.py",
        "quant_page.py",
        "quant/data.py",
        "quant/factors.py",
        "quant/backtest.py",
    ]
    return _required_files_item(
        root,
        label="preserved_entrypoints",
        required=required,
        pass_evidence=f"{len(required)} existing Streamlit, CLI, and quant entry files are present",
        next_step="restore missing legacy entrypoints before changing desktop packaging",
    )


def _check_desktop_launcher_mvp(root: Path) -> CompletionItem:
    required = [
        "启动NASDX桌面.bat",
        "desktop/launcher.py",
        "desktop/control.py",
        "desktop/control_panel.py",
        "desktop/exe_launcher.py",
        "desktop/runtime.py",
        "desktop/paths.py",
        "desktop/doctor.py",
        "run_desktop_doctor.py",
    ]
    missing = _missing_files(root, required)
    if missing:
        return CompletionItem(
            "desktop_launcher_mvp",
            FAIL,
            "missing: " + ", ".join(missing),
            "restore the thin launcher/control-panel files",
        )

    bat_text = _read_text(root / "启动NASDX桌面.bat")
    control_text = _read_text(root / "desktop" / "control.py")
    exe_text = _read_text(root / "desktop" / "exe_launcher.py")
    required_markers = ["%*", "desktop\\control_panel.py", "desktop\\launcher.py --webview --page plan"]
    missing_markers = [marker for marker in required_markers if marker not in bat_text]
    if missing_markers or "Data Refresh" not in control_text or "control_panel.py" not in exe_text:
        missing = missing_markers + ([] if "Data Refresh" in control_text else ["Data Refresh"])
        if "control_panel.py" not in exe_text:
            missing.append("exe_launcher control_panel.py")
        return CompletionItem(
            "desktop_launcher_mvp",
            FAIL,
            "missing source markers: " + ", ".join(missing),
            "keep the batch entry argument-forwarding and control-panel actions intact",
        )
    return CompletionItem(
        "desktop_launcher_mvp",
        PASS,
        "batch entry, launcher, launcher-exe shim, control panel, doctor, and Data Refresh action are present",
        "",
    )


def _check_safe_local_config(root: Path) -> CompletionItem:
    required = ["desktop/config.py", "config.example.toml"]
    if not _is_packaged_root(root):
        required.append(".gitignore")
    missing = _missing_files(root, required)
    if missing:
        return CompletionItem(
            "safe_local_config",
            FAIL,
            "missing: " + ", ".join(missing),
            "restore config loader, example config, and ignore rules",
        )

    config_text = _read_text(root / "desktop" / "config.py")
    if _is_packaged_root(root):
        manifest = _read_package_manifest(root)
        excluded = "\n".join(manifest.get("excluded_patterns", []))
        missing_markers = [marker for marker in ["config.toml", ".env"] if marker not in excluded]
        if missing_markers:
            return CompletionItem(
                "safe_local_config",
                FAIL,
                "package manifest missing exclusions: " + ", ".join(missing_markers),
                "update build_portable.ps1 excluded_patterns before packaging",
            )
        required_config_markers = ["CONFIG_FILE_ENV", "NASDX_CONFIG_FILE"]
        missing_config_markers = [marker for marker in required_config_markers if marker not in config_text]
        if missing_config_markers:
            return CompletionItem(
                "safe_local_config",
                FAIL,
                "config loader missing markers: " + ", ".join(missing_config_markers),
                "restore the desktop config loader contract",
            )
        return CompletionItem(
            "safe_local_config",
            PASS,
            "packaged config loader and manifest exclusions are present",
            "",
        )

    ignore_text = _read_text(root / ".gitignore")
    required_markers = ["config.toml", ".env", "CONFIG_FILE_ENV", "NASDX_CONFIG_FILE"]
    missing_markers = [
        marker
        for marker in required_markers
        if marker not in ignore_text and marker not in config_text
    ]
    if missing_markers:
        return CompletionItem(
            "safe_local_config",
            FAIL,
            "missing safety markers: " + ", ".join(missing_markers),
            "ensure real local config and env files stay untracked",
        )
    return CompletionItem(
        "safe_local_config",
        PASS,
        "local config loader, example config, and ignore rules are present",
        "",
    )


def _check_packaging_chain(root: Path) -> CompletionItem:
    if _is_packaged_root(root):
        required = [
            "requirements_desktop.txt",
            "packaging/windows/build_launcher_exe.ps1",
            "packaging/windows/create_shortcuts.ps1",
            "packaging/windows/smoke_installed.ps1",
            "packaging/windows/smoke_installer_roundtrip.ps1",
            "packaging/windows/constraints-win.txt",
            "docs/WINDOWS_DESKTOP.md",
            "PACKAGING_MANIFEST.json",
        ]
        return _required_files_item(
            root,
            label="packaging_chain",
            required=required,
            pass_evidence=f"{len(required)} packaged runtime and verification assets are present",
            next_step="rebuild the portable package from the source checkout",
        )

    required = [
        "requirements_desktop.txt",
        "packaging/windows/build_launcher_exe.ps1",
        "packaging/windows/build_portable.ps1",
        "packaging/windows/build_portable_zip.ps1",
        "packaging/windows/smoke_portable.ps1",
        "packaging/windows/smoke_portable_zip.ps1",
        "packaging/windows/smoke_installed.ps1",
        "packaging/windows/smoke_installer_roundtrip.ps1",
        "packaging/windows/install_inno_setup.ps1",
        "packaging/windows/preflight_installer_release.ps1",
        "packaging/windows/build_wheelhouse.ps1",
        "packaging/windows/build_installer.ps1",
        "packaging/windows/NASDX-Desktop.iss",
        "packaging/windows/constraints-win.txt",
        "packaging/windows/README.md",
        "docs/WINDOWS_DESKTOP.md",
    ]
    return _required_files_item(
        root,
        label="packaging_chain",
        required=required,
        pass_evidence=f"{len(required)} portable, launcher-exe, installed-layout, installer, and documentation assets are present",
        next_step="restore packaging scripts before producing release artifacts",
    )


def _check_test_release_gates(root: Path) -> CompletionItem:
    if _is_packaged_root(root):
        required = [
            "run_security_checks.py",
            "run_desktop_release_check.py",
            "run_desktop_doctor.py",
            "run_desktop_completion_audit.py",
            "run_desktop_release_evidence.py",
            "run_final_audit.py",
        ]
        missing = _missing_files(root, required)
        if missing:
            return CompletionItem(
                "test_release_gates",
                FAIL,
                "packaged release helper missing: " + ", ".join(missing),
                "ensure build_portable.ps1 copies root release-helper scripts",
            )
        return CompletionItem(
            "test_release_gates",
            WARN,
            "source-only tests and pre-commit config are not packaged; packaged release helpers are present",
            "run full pytest and pre-commit checks from the source checkout before building artifacts",
        )

    required = [
        "pyproject.toml",
        "requirements-dev.txt",
        ".pre-commit-config.yaml",
        "run_security_checks.py",
        "run_desktop_release_check.py",
        "run_desktop_release_evidence.py",
        "run_final_audit.py",
        "tests/test_quant_core_contracts.py",
        "tests/test_desktop_launcher_contracts.py",
        "tests/test_desktop_control_contracts.py",
        "tests/test_desktop_packaging_contracts.py",
        "tests/test_desktop_release_check_contracts.py",
        "tests/test_desktop_release_evidence_contracts.py",
        "tests/test_desktop_doctor_contracts.py",
        "tests/test_security_checks_contracts.py",
    ]
    missing = _missing_files(root, required)
    if missing:
        return CompletionItem(
            "test_release_gates",
            FAIL,
            "missing: " + ", ".join(missing),
            "restore the test and release gate files",
        )

    release_text = _read_text(root / "run_desktop_release_check.py")
    required_markers = [
        "ruff",
        "desktop_contracts",
        "security_checks",
        "desktop_doctor",
        "release_evidence",
        "--package-dir",
        "--write-evidence",
        "--evidence-output",
        "portable_smoke",
    ]
    missing_markers = [marker for marker in required_markers if marker not in release_text]
    if missing_markers:
        return CompletionItem(
            "test_release_gates",
            FAIL,
            "release gate missing: " + ", ".join(missing_markers),
            "keep the default release check broad enough for desktop delivery",
        )
    return CompletionItem(
        "test_release_gates",
        PASS,
        "dev tooling, contract tests, security scan, doctor, release evidence, and release gate are present",
        "",
    )


def _check_portable_runtime_bundle(root: Path) -> CompletionItem:
    if _is_packaged_root(root):
        return _check_package_venv(root, label="portable_runtime_bundle")

    package_root = root / "dist" / "NASDX-Desktop"
    if not package_root.exists():
        return CompletionItem(
            "portable_runtime_bundle",
            INCOMPLETE,
            "dist/NASDX-Desktop has not been built yet",
            "run run_desktop_release_check.py --full-package with package and pip timeout options",
        )
    if not (package_root / "PACKAGING_MANIFEST.json").exists():
        return CompletionItem(
            "portable_runtime_bundle",
            INCOMPLETE,
            "dist/NASDX-Desktop exists but has no PACKAGING_MANIFEST.json",
            "rebuild the portable package from the source checkout",
        )
    manifest = _read_package_manifest(package_root)
    if manifest.get("skip_dependency_install") is True:
        return CompletionItem(
            "portable_runtime_bundle",
            INCOMPLETE,
            "current portable package was built with -SkipDependencyInstall",
            "run run_desktop_release_check.py --full-package --package-timeout 1200 --pip-timeout 120 --pip-retries 3",
        )
    return _check_package_venv(package_root, label="portable_runtime_bundle")


def _check_package_venv(root: Path, *, label: str) -> CompletionItem:
    python_path = root / ".venv" / "Scripts" / "python.exe"
    if python_path.exists():
        return CompletionItem(label, PASS, f"bundled Python found: {python_path}", "")
    return CompletionItem(
        label,
        INCOMPLETE,
        f"bundled Python is missing: {python_path}",
        "build the portable package without -SkipDependencyInstall and run smoke with -RequireVenv",
    )


def _check_generated_files_excluded(root: Path) -> CompletionItem:
    if _is_packaged_root(root):
        manifest = _read_package_manifest(root)
        excluded = "\n".join(manifest.get("excluded_patterns", []))
        required_patterns = [
            "reports/",
            "stock_data_*.json",
            "nasdx_history.db",
            "config.toml",
            ".env",
            "__pycache__/",
            "*.pyc",
            "desktop_logs/",
            "wheelhouse/",
        ]
        missing_patterns = [pattern for pattern in required_patterns if pattern not in excluded]
        forbidden_present = [
            path
            for path in ["reports", "stock_data_20990101.json", "nasdx_history.db", "config.toml", ".env"]
            if (root / path).exists()
        ]
        forbidden_present.extend(_package_cache_artifacts(root))
        if missing_patterns or forbidden_present:
            detail_parts = []
            if missing_patterns:
                detail_parts.append("manifest missing: " + ", ".join(missing_patterns))
            if forbidden_present:
                detail_parts.append("present in package: " + ", ".join(forbidden_present))
            return CompletionItem(
                "generated_files_excluded",
                FAIL,
                "; ".join(detail_parts),
                "rebuild the package after fixing build_portable.ps1 exclusions",
            )
        return CompletionItem(
            "generated_files_excluded",
            PASS,
            "package manifest excludes runtime artifacts and forbidden files are absent",
            "",
        )

    ignored_paths = [
        "reports/example.json",
        "stock_data_20990101.json",
        "nasdx_history.db",
        "config.toml",
        ".env",
        "dist/NASDX-Desktop",
        "dist/NASDX-Desktop-portable.zip",
        "dist/NASDX-Desktop-portable.zip.sha256",
        "dist/NASDX-Desktop-portable.manifest.json",
        "dist/installer/NASDX-Desktop-Setup.exe",
        "desktop_logs/launcher.log",
        "wheelhouse/example.whl",
        "models/signal_confidence.json",
        "dist/release-evidence/NASDX-desktop-release-evidence.json",
    ]
    not_ignored = [path for path in ignored_paths if not _is_git_ignored(root, path)]
    if not_ignored:
        return CompletionItem(
            "generated_files_excluded",
            FAIL,
            "not ignored: " + ", ".join(not_ignored),
            "update .gitignore before sharing desktop artifacts",
        )
    return CompletionItem(
        "generated_files_excluded",
        PASS,
        f"{len(ignored_paths)} secret/runtime/report/cache/build paths are ignored",
        "",
    )


def _package_cache_artifacts(root: Path) -> list[str]:
    if not root.exists():
        return []
    matches = []
    for path in root.rglob("__pycache__"):
        if path.is_dir():
            matches.append(_rel_to(path, root))
    for pattern in ("*.pyc", "*.pyo"):
        for path in root.rglob(pattern):
            if path.is_file():
                matches.append(_rel_to(path, root))
    return sorted(set(matches))


def _check_optional_webview() -> CompletionItem:
    if importlib.util.find_spec("webview") is None:
        return CompletionItem(
            "optional_webview",
            WARN,
            "pywebview is not installed; browser fallback remains the expected MVP path",
            "install requirements_desktop.txt or package with -IncludeWebView when native webview is required",
        )
    return CompletionItem("optional_webview", PASS, "pywebview is importable", "")


def _check_installer_compile_tool() -> CompletionItem:
    iscc_path = _find_iscc()
    if iscc_path:
        return CompletionItem("installer_compile_tool", PASS, f"ISCC found at {iscc_path}", "")
    return CompletionItem(
        "installer_compile_tool",
        INCOMPLETE,
        "ISCC.exe was not found on this machine",
        "run install_inno_setup.ps1 in plan-only mode, then explicitly pass -Install -AcceptAgreements or provide -IsccPath",
    )


def _check_installer_roundtrip(root: Path) -> CompletionItem:
    script = root / "packaging" / "windows" / "smoke_installer_roundtrip.ps1"
    installer = root / "dist" / "installer" / "NASDX-Desktop-Setup.exe"
    proof = root / "dist" / "installer" / "NASDX-Desktop-roundtrip-proof.json"
    if not script.exists():
        return CompletionItem(
            "installer_roundtrip",
            FAIL,
            "roundtrip smoke script is missing",
            "restore packaging/windows/smoke_installer_roundtrip.ps1",
        )
    proof_item = _check_installer_roundtrip_proof(installer, proof)
    if proof_item:
        return proof_item
    if installer.exists() and _find_iscc():
        return CompletionItem(
            "installer_roundtrip",
            WARN,
            "installer executable exists, but install/uninstall roundtrip is not proven by this read-only audit",
            "run smoke_installer_roundtrip.ps1 -AllowInstall -CheckShortcuts -RequireVenv in a disposable profile or VM",
        )
    return CompletionItem(
        "installer_roundtrip",
        INCOMPLETE,
        "real installer install/smoke/uninstall proof is still pending",
        "compile the setup executable and run smoke_installer_roundtrip.ps1 -AllowInstall -CheckShortcuts -RequireVenv",
    )


def _check_installer_roundtrip_proof(installer: Path, proof: Path) -> CompletionItem | None:
    if not proof.exists():
        return None
    if not installer.exists():
        return CompletionItem(
            "installer_roundtrip",
            INCOMPLETE,
            f"roundtrip proof exists but installer is missing: {proof}",
            "rebuild the setup executable or remove stale proof before release",
        )
    try:
        payload = json.loads(_read_text(proof))
    except json.JSONDecodeError as exc:
        return CompletionItem(
            "installer_roundtrip",
            FAIL,
            f"roundtrip proof is not valid JSON: {exc}",
            "rerun smoke_installer_roundtrip.ps1 -AllowInstall -CheckShortcuts -RequireVenv",
        )

    required = {
        "schema": "nasdx_installer_roundtrip_proof.v1",
        "installed_smoke": "passed",
        "uninstall": "passed",
    }
    mismatched = [name for name, value in required.items() if payload.get(name) != value]
    if mismatched:
        return CompletionItem(
            "installer_roundtrip",
            FAIL,
            "roundtrip proof has invalid fields: " + ", ".join(mismatched),
            "rerun smoke_installer_roundtrip.ps1 -AllowInstall -CheckShortcuts -RequireVenv",
        )
    required_true = ["require_venv", "check_shortcuts"]
    missing_true = [name for name in required_true if payload.get(name) is not True]
    if payload.get("kept_installed") is True:
        missing_true.append("kept_installed=false")
    if missing_true:
        return CompletionItem(
            "installer_roundtrip",
            INCOMPLETE,
            "roundtrip proof is not final-release proof: " + ", ".join(missing_true),
            "rerun smoke_installer_roundtrip.ps1 -AllowInstall -CheckShortcuts -RequireVenv without -KeepInstalled",
        )

    actual_hash = hashlib.sha256(installer.read_bytes()).hexdigest()
    proof_hash = str(payload.get("installer_sha256", "")).lower()
    if proof_hash != actual_hash:
        return CompletionItem(
            "installer_roundtrip",
            FAIL,
            "roundtrip proof installer hash does not match current setup executable",
            "rerun installer compile and smoke_installer_roundtrip.ps1 for the current artifact",
        )
    return CompletionItem(
        "installer_roundtrip",
        PASS,
        f"verified installer roundtrip proof: {proof}",
        "",
    )


def _required_files_item(
    root: Path,
    *,
    label: str,
    required: list[str],
    pass_evidence: str,
    next_step: str,
) -> CompletionItem:
    missing = _missing_files(root, required)
    if missing:
        return CompletionItem(label, FAIL, "missing: " + ", ".join(missing), next_step)
    return CompletionItem(label, PASS, pass_evidence, "")


def _missing_files(root: Path, required: list[str]) -> list[str]:
    return [item for item in required if not (root / item).exists()]


def _is_packaged_root(root: Path) -> bool:
    return (root / "PACKAGING_MANIFEST.json").exists() and not (root / ".git").exists()


def _read_package_manifest(root: Path) -> dict:
    manifest_path = root / "PACKAGING_MANIFEST.json"
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(_read_text(manifest_path))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig")


def _rel_to(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _is_git_ignored(root: Path, path: str) -> bool:
    proc = subprocess.run(
        ["git", "check-ignore", "-q", path],
        cwd=str(root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.returncode == 0


def _find_iscc() -> str | None:
    return find_iscc()


def _statuses() -> tuple[str, ...]:
    return PASS, WARN, INCOMPLETE, FAIL


if __name__ == "__main__":
    raise SystemExit(main())
